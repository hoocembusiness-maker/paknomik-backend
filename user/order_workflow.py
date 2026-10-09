"""Order execution workflow: allowed status transitions and history.

Recording or tracking an order never touches the Pragmadinar wallet.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from user.models import Order, OrderStatusEvent, TradeScope

logger = logging.getLogger(__name__)

ORDER_STATUS_LABELS = {
    Order.Status.PENDING: "En attente",
    Order.Status.CONFIRMED: "Confirmée",
    Order.Status.IN_NEGOTIATION: "En négociation",
    Order.Status.WON: "Gagnée",
    Order.Status.LOST: "Perdue",
    Order.Status.IN_PRODUCTION: "En production",
    Order.Status.SHIPPED: "Expédiée",
    Order.Status.DELIVERED: "Livrée",
    Order.Status.COMPLETED: "Terminée",
    Order.Status.CANCELLED: "Annulée",
    Order.Status.EXPIRED: "Expirée",
}

SUPPLIER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    Order.Status.PENDING: (Order.Status.CONFIRMED, Order.Status.CANCELLED),
    Order.Status.IN_NEGOTIATION: (Order.Status.CONFIRMED, Order.Status.CANCELLED),
    Order.Status.WON: (
        Order.Status.CONFIRMED,
        Order.Status.IN_PRODUCTION,
        Order.Status.CANCELLED,
    ),
    Order.Status.CONFIRMED: (
        Order.Status.IN_PRODUCTION,
        Order.Status.SHIPPED,
        Order.Status.CANCELLED,
    ),
    Order.Status.IN_PRODUCTION: (Order.Status.SHIPPED, Order.Status.CANCELLED),
    Order.Status.SHIPPED: (Order.Status.DELIVERED,),
    Order.Status.DELIVERED: (Order.Status.COMPLETED,),
}

SUPPLIER_REOPEN_TRANSITIONS: dict[str, tuple[str, ...]] = {
    Order.Status.CANCELLED: (Order.Status.PENDING,),
}

SUPPLIER_ARCHIVABLE = {
    Order.Status.CANCELLED,
    Order.Status.LOST,
    Order.Status.EXPIRED,
    Order.Status.COMPLETED,
}

BUYER_CANCELLABLE = {
    Order.Status.PENDING,
    Order.Status.IN_NEGOTIATION,
    Order.Status.CONFIRMED,
    Order.Status.WON,
}

EXECUTION_STEPS = (
    Order.Status.CONFIRMED,
    Order.Status.IN_PRODUCTION,
    Order.Status.SHIPPED,
    Order.Status.DELIVERED,
    Order.Status.COMPLETED,
)


AMOUNT_LOCKED_STATUSES = {
    Order.Status.CANCELLED,
    Order.Status.EXPIRED,
    Order.Status.LOST,
    Order.Status.COMPLETED,
}
MAX_UNIT_PRICE = Decimal("99999999.99")


class OrderTransitionError(ValueError):
    pass


class OrderAmountError(ValueError):
    pass


def order_amount_editable(order: Order) -> bool:
    """The supplier sets the price when the product sheet has no fixed price."""
    if order.status in AMOUNT_LOCKED_STATUSES:
        return False
    if (order.commercial_snapshot or {}).get("totals"):
        return False
    return order.product.fixed_unit_price is None


def _format_dzd(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " DA"


def update_order_unit_price(order: Order, raw_price, *, actor=None, note: str = "", request=None):
    """Set the agreed unit price of an order whose product has no fixed price."""
    from user import crm, rbac
    from user.commission import refresh_commission_reference
    from user.models import Message

    if not order_amount_editable(order):
        raise OrderAmountError(
            "Le montant de cette commande ne peut pas être modifié : le prix est fixé dans la "
            "fiche produit, issu d'un devis accepté, ou la commande est clôturée."
        )
    try:
        unit_price = Decimal(str(raw_price).replace(",", ".").replace(" ", "")).quantize(
            Decimal("0.01")
        )
    except (InvalidOperation, ValueError) as exc:
        raise OrderAmountError("Prix unitaire invalide.") from exc
    if unit_price <= 0 or unit_price > MAX_UNIT_PRICE:
        raise OrderAmountError("Le prix unitaire doit être supérieur à 0.")

    previous = order.unit_price
    with transaction.atomic():
        order.unit_price = unit_price
        order.save(update_fields=["unit_price", "updated_at"])
        refresh_commission_reference(order)
    total = unit_price * order.quantity

    try:
        opportunity = crm.resolve_opportunity_for_order(order)
        if opportunity is not None:
            opportunity.estimated_value = total
            opportunity.save(update_fields=["estimated_value", "updated_at"])
    except Exception:
        logger.exception("CRM value sync failed for order %s", order.pk)

    rbac.log_audit(
        actor,
        "order.amount_update",
        entity_type="order",
        entity_id=order.pk,
        old_value={"unitPrice": str(previous)},
        new_value={"unitPrice": str(unit_price), "total": str(total)},
        reason=note,
        request=request,
    )

    if order.buyer_id and actor is not None:
        content = (
            f"Commande #{order.pk} — montant mis à jour : {_format_dzd(total)} "
            f"({_format_dzd(unit_price)} × {order.quantity} u.)."
        )
        if note:
            content += f"\n{note}"
        Message.objects.create(
            supplier=order.supplier, buyer=order.buyer, sender=actor, content=content
        )
    return order


def status_label(value: str) -> str:
    return ORDER_STATUS_LABELS.get(value, value)


def supplier_allowed_transitions(order: Order) -> list[str]:
    return [str(s) for s in SUPPLIER_TRANSITIONS.get(order.status, ())]


def record_status_event(
    order: Order,
    *,
    from_status: str,
    to_status: str,
    actor=None,
    actor_role: str = "",
    note: str = "",
) -> OrderStatusEvent:
    return OrderStatusEvent.objects.create(
        order=order,
        from_status=from_status or "",
        to_status=to_status,
        actor=actor,
        actor_role=actor_role,
        note=(note or "").strip(),
    )


def transition_order(
    order: Order,
    to_status: str,
    *,
    actor=None,
    actor_role: str,
    note: str = "",
    allowed: dict[str, tuple[str, ...]] | None = None,
) -> OrderStatusEvent:
    rules = SUPPLIER_TRANSITIONS if allowed is None else allowed
    if to_status not in rules.get(order.status, ()):
        raise OrderTransitionError(
            f"Passage de « {status_label(order.status)} » à "
            f"« {status_label(to_status)} » non autorisé."
        )
    from_status = order.status
    with transaction.atomic():
        order.status = to_status
        order.save(update_fields=["status", "updated_at"])
        return record_status_event(
            order,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            actor_role=actor_role,
            note=note,
        )


def order_summary_note(order: Order) -> str:
    """Chat note sent to the supplier with the request details."""
    from user.export_terms import INCOTERM_TO_DEFINE_LABEL

    if order.trade_scope == TradeScope.EXPORT:
        lines = [
            f"Demande de commande export #{order.pk} — {order.quantity} u. · {order.product.name}.",
            f"Destination : {order.destination_country}"
            + (f" — {order.destination_city}" if order.destination_city else ""),
            f"Incoterm souhaité : {order.incoterm or INCOTERM_TO_DEFINE_LABEL}",
        ]
    else:
        lines = [f"Commande #{order.pk} — {order.quantity} unités · {order.product.name}."]
    if order.specifications.strip():
        lines.append(f"Préférence technique (facultative) : {order.specifications.strip()}")
    if order.buyer_note.strip():
        lines.append(f"Commentaire : {order.buyer_note.strip()}")
    return "\n".join(lines)


def serialize_status_history(order: Order) -> list[dict]:
    return [
        {
            "id": event.id,
            "fromStatus": event.from_status or None,
            "toStatus": event.to_status,
            "label": status_label(event.to_status),
            "actorRole": event.actor_role or "",
            "note": event.note or "",
            "createdAt": event.created_at.isoformat(),
        }
        for event in order.status_events.all()
    ]
