"""Commission tracking for orders originated on Paknomik.

Paknomik never collects payments and is not the transaction operator. Every
order born on Paknomik (product page, quote demand, storefront quote) — local
or export, whatever the supplier's level — records a commission line owed by
the supplier under its active mandate. The line is never billed to, nor shown
to, the buyer, and recording it never blocks the order.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from user import rbac
from user.platform_config import get_optional_decimal

logger = logging.getLogger(__name__)

MONEY = Decimal("0.01")

REASON_NO_MANDATE = "Aucun mandat actif pour ce fournisseur : commission à valider."
REASON_RATE_OUT_OF_RANGE = "Taux du mandat hors de la plage configurée : à valider."
REASON_CUSTOM_BASE = "Base de calcul spécifique au mandat : montant à calculer."
REASON_NO_AMOUNT = "Montant de référence inconnu : montant à calculer."


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def rate_bounds() -> tuple[Decimal | None, Decimal | None]:
    return (
        get_optional_decimal("commission_rate_min_percent"),
        get_optional_decimal("commission_rate_max_percent"),
    )


def rate_within_bounds(rate) -> bool:
    low, high = rate_bounds()
    rate = Decimal(str(rate))
    if low is not None and rate < low:
        return False
    if high is not None and rate > high:
        return False
    return True


def active_mandate_for(supplier, trade_scope: str, *, on_date=None):
    from user.models import SupplierMandate

    today = on_date or timezone.localdate()
    return (
        SupplierMandate.objects.filter(
            supplier=supplier,
            status=SupplierMandate.Status.ACTIVE,
            scope__in=[SupplierMandate.Scope.ALL, trade_scope],
        )
        .filter(Q(starts_on__isnull=True) | Q(starts_on__lte=today))
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        .order_by("-starts_on", "-created_at")
        .first()
    )


def reference_amount_for(order) -> tuple[Decimal | None, str]:
    """Transaction reference amount: the agreed total, else quantity × unit price."""
    snapshot = order.commercial_snapshot or {}
    currency = snapshot.get("currency") or "DZD"
    total = (snapshot.get("totals") or {}).get("totalAmount")
    if total not in (None, ""):
        try:
            return _money(total), currency
        except Exception:
            pass
    unit_price = order.unit_price or Decimal("0")
    if unit_price > 0 and order.quantity:
        return _money(unit_price * order.quantity), currency
    return None, currency


def _apply_calculation(commission) -> None:
    """Fill amount/status from the stored rate and reference amount."""
    from user.models import OrderCommission

    if commission.rate is None:
        commission.amount = None
        commission.status = OrderCommission.Status.TO_VALIDATE
        commission.status_reason = commission.status_reason or REASON_NO_MANDATE
        return
    if not rate_within_bounds(commission.rate):
        commission.amount = None
        commission.status = OrderCommission.Status.TO_VALIDATE
        commission.status_reason = REASON_RATE_OUT_OF_RANGE
        return
    if commission.calculation_base == "custom":
        commission.amount = None
        commission.status = OrderCommission.Status.TO_VALIDATE
        commission.status_reason = REASON_CUSTOM_BASE
        return
    if commission.reference_amount is None:
        commission.amount = None
        commission.status = OrderCommission.Status.TO_VALIDATE
        commission.status_reason = REASON_NO_AMOUNT
        return
    commission.amount = _money(commission.reference_amount * commission.rate / Decimal("100"))
    commission.status = OrderCommission.Status.CALCULATED
    commission.status_reason = ""


def register_order_commission(order):
    """Create the commission line of a Paknomik-born order (idempotent).

    Returns None for external orders. Never raises: a failure is logged and the
    order stays recorded.
    """
    from user.models import OrderCommission

    if not order.is_paknomik_origin:
        return None
    try:
        with transaction.atomic():
            existing = OrderCommission.objects.filter(order=order).first()
            if existing:
                return existing
            mandate = active_mandate_for(order.supplier, order.trade_scope)
            reference, currency = reference_amount_for(order)
            commission = OrderCommission(
                order=order,
                mandate=mandate,
                trade_scope=order.trade_scope,
                rate=mandate.commission_rate if mandate else None,
                calculation_base=mandate.calculation_base if mandate else "",
                reference_amount=reference,
                currency=currency,
            )
            _apply_calculation(commission)
            commission.save()
            return commission
    except Exception:
        logger.exception("Commission registration failed for order %s", order.pk)
        return None


def refresh_commission_reference(order):
    """Recompute an unvalidated commission after the order amount changed."""
    from user.models import OrderCommission

    commission = OrderCommission.objects.filter(order=order).first()
    if commission is None or commission.status not in (
        OrderCommission.Status.TO_VALIDATE,
        OrderCommission.Status.CALCULATED,
    ):
        return commission
    commission.reference_amount, commission.currency = reference_amount_for(order)
    if commission.status_reason == REASON_NO_AMOUNT:
        commission.status_reason = ""
    _apply_calculation(commission)
    commission.save()
    return commission


@transaction.atomic
def review_commission(commission, actor, data: dict, *, request=None):
    """Administrator review: attach a mandate, correct the reference amount,
    validate / mark not due, update the settlement status. Audited."""
    from user.models import OrderCommission, SupplierMandate

    commission = OrderCommission.objects.select_for_update().get(pk=commission.pk)
    before = serialize_commission(commission, include_admin=True)
    locked = commission.status == OrderCommission.Status.VALIDATED

    mandate_id = data.get("mandateId")
    if mandate_id not in (None, "") and not locked:
        mandate = SupplierMandate.objects.filter(
            pk=mandate_id, supplier_id=commission.order.supplier_id
        ).first()
        if mandate is None:
            raise ValidationError(
                {"error": "Mandat introuvable pour ce fournisseur.", "code": "invalid_mandate"}
            )
        commission.mandate = mandate
        # The rate stays the one applicable at order time once it is known.
        if commission.rate is None:
            commission.rate = mandate.commission_rate
            commission.calculation_base = mandate.calculation_base
        commission.status_reason = ""

    if "referenceAmount" in data and not locked:
        raw = data.get("referenceAmount")
        if raw in (None, ""):
            commission.reference_amount = None
        else:
            try:
                amount = _money(raw)
            except Exception as exc:
                raise ValidationError(
                    {"error": "Montant de référence invalide.", "code": "invalid_amount"}
                ) from exc
            if amount < 0:
                raise ValidationError(
                    {"error": "Montant de référence invalide.", "code": "invalid_amount"}
                )
            commission.reference_amount = amount

    recalculate = mandate_id not in (None, "") or "referenceAmount" in data
    if recalculate and not locked:
        _apply_calculation(commission)

    if "amount" in data and not locked and data.get("amount") not in (None, ""):
        try:
            commission.amount = _money(data["amount"])
        except Exception as exc:
            raise ValidationError(
                {"error": "Montant de commission invalide.", "code": "invalid_amount"}
            ) from exc

    target = data.get("status")
    if target:
        if target not in OrderCommission.Status.values:
            raise ValidationError({"error": "Statut inconnu.", "code": "invalid_status"})
        if target == OrderCommission.Status.VALIDATED:
            if commission.amount is None:
                raise ValidationError(
                    {
                        "error": "Calculez ou saisissez le montant avant de valider.",
                        "code": "amount_required",
                    }
                )
            commission.validated_by = actor
            commission.validated_at = timezone.now()
        commission.status = target

    settlement = data.get("settlementStatus")
    if settlement:
        if settlement not in OrderCommission.Settlement.values:
            raise ValidationError(
                {"error": "Statut de règlement inconnu.", "code": "invalid_settlement"}
            )
        commission.settlement_status = settlement
        if settlement == OrderCommission.Settlement.SETTLED:
            commission.settled_at = commission.settled_at or timezone.now()

    if "adminNote" in data:
        commission.admin_note = str(data.get("adminNote") or "")[:2000]

    commission.save()
    rbac.log_audit(
        actor,
        "commission.review",
        entity_type="order_commission",
        entity_id=commission.pk,
        old_value=before,
        new_value=serialize_commission(commission, include_admin=True),
        request=request,
    )
    return commission


def _decimal_str(value):
    return str(value) if value is not None else None


def serialize_commission(commission, *, include_admin: bool = False) -> dict:
    order = commission.order
    data = {
        "id": commission.pk,
        "orderId": commission.order_id,
        "origin": order.origin,
        "originLabel": order.get_origin_display(),
        "tradeScope": commission.trade_scope,
        "destinationCountry": order.destination_country or "",
        "mandateId": commission.mandate_id,
        "mandateReference": commission.mandate.reference if commission.mandate_id else "",
        "rate": _decimal_str(commission.rate),
        "calculationBase": commission.calculation_base,
        "referenceAmount": _decimal_str(commission.reference_amount),
        "currency": commission.currency,
        "amount": _decimal_str(commission.amount),
        "status": commission.status,
        "statusLabel": commission.get_status_display(),
        "statusReason": commission.status_reason,
        "settlementStatus": commission.settlement_status,
        "settlementLabel": commission.get_settlement_status_display(),
        "createdAt": commission.created_at.isoformat() if commission.created_at else None,
    }
    if include_admin:
        data.update(
            {
                "supplierId": order.supplier_id,
                "supplierName": order.supplier.company_name if order.supplier_id else "",
                "buyerId": order.buyer_id,
                "validatedAt": (
                    commission.validated_at.isoformat() if commission.validated_at else None
                ),
                "validatedBy": commission.validated_by_id,
                "settledAt": commission.settled_at.isoformat() if commission.settled_at else None,
                "adminNote": commission.admin_note,
            }
        )
    return data


def serialize_mandate(mandate) -> dict:
    return {
        "id": mandate.pk,
        "supplierId": mandate.supplier_id,
        "supplierName": mandate.supplier.company_name,
        "reference": mandate.reference,
        "scope": mandate.scope,
        "commissionRate": str(mandate.commission_rate),
        "calculationBase": mandate.calculation_base,
        "calculationTerms": mandate.calculation_terms,
        "status": mandate.status,
        "startsOn": mandate.starts_on.isoformat() if mandate.starts_on else None,
        "endsOn": mandate.ends_on.isoformat() if mandate.ends_on else None,
        "notes": mandate.notes,
        "createdAt": mandate.created_at.isoformat() if mandate.created_at else None,
    }
