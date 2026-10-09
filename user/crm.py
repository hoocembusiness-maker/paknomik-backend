"""
Supplier CRM logic (contacts, companies, deals, activities).

Each supplier only sees their own CRM data. This file loads and saves CRM
records safely and respects buyer privacy until consent is given.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from user.platform_config import get_int_list

logger = logging.getLogger(__name__)

# ── Ownership scoping ───────────────────────────────────────────────────────


def companies_for(supplier, *, include_archived=False):
    from user.models import CrmCompany

    qs = CrmCompany.objects.filter(supplier=supplier)
    return qs if include_archived else qs.filter(is_archived=False)


def contacts_for(supplier, *, include_archived=False):
    from user.models import CrmContact

    qs = CrmContact.objects.filter(supplier=supplier).select_related("company")
    return qs if include_archived else qs.filter(is_archived=False)


def opportunities_for(supplier):
    from user.models import Opportunity

    return Opportunity.objects.filter(supplier=supplier).select_related(
        "company", "contact", "demand"
    )


def activities_for(supplier):
    from user.models import CrmActivity

    return CrmActivity.objects.filter(supplier=supplier).select_related(
        "opportunity", "company", "contact"
    )


# ── Buyer privacy ───────────────────────────────────────────────────────────


def can_reveal_contact(contact) -> bool:
    """True only when the buyer authorised this supplier to see coordinates."""
    if contact.contact_consent:
        return True
    if contact.user_id is None:
        # Manually entered by the supplier: it is the supplier's own data.
        return True
    return False


def contact_payload(contact) -> dict:
    """Serialize a contact, masking anything the buyer did not authorise."""
    revealed = can_reveal_contact(contact)
    payload = {
        "id": contact.id,
        "companyId": contact.company_id,
        "companyName": contact.company.name if contact.company_id else "",
        "firstName": contact.first_name,
        "lastName": contact.last_name,
        "jobTitle": contact.job_title,
        "decisionRole": contact.decision_role,
        "preferredChannel": contact.preferred_channel,
        "consent": contact.contact_consent,
        "consentUpdatedAt": contact.consent_updated_at,
        "lastInteractionAt": contact.last_interaction_at,
        "notes": contact.notes,
        "isPlatformBuyer": contact.user_id is not None,
        "buyerId": contact.user_id,
        "createdAt": contact.created_at,
        "displayName": (
            f"{contact.first_name} {contact.last_name}".strip()
            or (contact.company.name if contact.company_id else "")
            or "Contact sans nom"
        ),
    }
    if revealed:
        payload.update(
            {
                "email": contact.email,
                "phone": contact.phone,
                "whatsapp": contact.whatsapp,
                "masked": False,
            }
        )
    else:
        payload.update(
            {
                "email": "",
                "phone": "",
                "whatsapp": "",
                "masked": True,
                "maskedReason": (
                    "L'acheteur n'a pas autorisé le partage de ses coordonnées. "
                    "Échangez via la messagerie Paknomik."
                ),
            }
        )
    return payload


# ── Creation / synchronisation ──────────────────────────────────────────────


def _buyer_full_name(buyer) -> str:
    return f"{getattr(buyer, 'first_name', '') or ''} {getattr(buyer, 'last_name', '') or ''}".strip()


def _buyer_company_name(buyer) -> str:
    company = (getattr(buyer, "company_name", None) or "").strip()
    if company:
        return company[:255]
    full = _buyer_full_name(buyer)
    if full:
        return full[:255]
    username = (getattr(buyer, "username", None) or "").strip()
    if username and "@" not in username:
        return username[:255]
    return f"Acheteur #{buyer.pk}"


def _refresh_company_name(company, name=""):
    name = (name or "").strip()
    if not name or name == company.name:
        return company
    buyer_name = _buyer_full_name(company.buyer) if company.buyer_id else ""
    weak = (
        not company.name
        or "@" in company.name
        or company.name.startswith("Acheteur #")
        or (buyer_name and company.name == buyer_name and name != buyer_name)
    )
    if weak:
        company.name = name[:255]
        company.save(update_fields=["name", "updated_at"])
    return company


@transaction.atomic
def ensure_company(supplier, *, buyer=None, name="", **defaults):
    """Idempotent: one company per (supplier, buyer) when the buyer is known."""
    from user.models import CrmCompany

    if buyer is not None:
        company, created = CrmCompany.objects.get_or_create(
            supplier=supplier,
            buyer=buyer,
            defaults={
                "name": name or _buyer_company_name(buyer),
                "wilaya": getattr(buyer, "wilaya", "") or "",
                "status": CrmCompany.Status.PROSPECT,
                **defaults,
            },
        )
        if not created:
            _refresh_company_name(company, name or _buyer_company_name(buyer))
        return company, created

    company, created = CrmCompany.objects.get_or_create(
        supplier=supplier,
        buyer__isnull=True,
        name=name,
        defaults={"status": CrmCompany.Status.PROSPECT, **defaults},
    )
    return company, created


@transaction.atomic
def sync_contact_from_buyer(
    supplier,
    *,
    buyer=None,
    guest_name="",
    guest_email="",
    guest_phone="",
    company_name="",
    consent=False,
    interaction_title="",
    interaction_body="",
):
    """MVP entry point: one CRM contact row per platform buyer (company kept internal)."""
    from user.models import CrmActivity

    now = timezone.now()
    if buyer is not None:
        company, _ = ensure_company(
            supplier,
            buyer=buyer,
            name=company_name or _buyer_company_name(buyer),
        )
        contact, _ = ensure_contact(
            supplier,
            company,
            buyer=buyer,
            consent=consent,
        )
    else:
        label = (guest_name or "Client invité").strip()[:255]
        company, _ = ensure_company(supplier, name=label)
        parts = label.split(None, 1)
        first = parts[0] if parts else label
        last = parts[1] if len(parts) > 1 else ""
        contact, _ = ensure_contact(
            supplier,
            company,
            consent=True,
            first_name=first[:120],
            last_name=last[:120],
            email=(guest_email or "")[:254],
            phone=(guest_phone or "")[:40],
        )

    contact.last_interaction_at = now
    contact.save(update_fields=["last_interaction_at", "updated_at"])

    if interaction_title:
        record_activity(
            supplier,
            kind=CrmActivity.Kind.SYSTEM,
            title=interaction_title[:255],
            body=(interaction_body or "")[:2000],
            company=contact.company,
            contact=contact,
        )
    return contact


@transaction.atomic
def sync_from_rfq(rfq):
    """Legacy boutique RFQ (`RFQRequest`) → CRM contact."""
    supplier = rfq.supplier
    title = "Demande de devis reçue"
    body = (rfq.message or "").strip()
    if rfq.product_id and rfq.product:
        body = f"Produit : {rfq.product.name}\n\n{body}".strip()

    if rfq.buyer_id:
        contact = sync_contact_from_buyer(
            supplier,
            buyer=rfq.buyer,
            company_name=(rfq.company or "").strip(),
            consent=True,
            interaction_title=title,
            interaction_body=body,
        )
    else:
        contact = sync_contact_from_buyer(
            supplier,
            guest_name=(rfq.name or "").strip(),
            guest_email=(rfq.email or "").strip(),
            guest_phone=(rfq.phone or "").strip(),
            consent=True,
            interaction_title=title,
            interaction_body=body,
        )

    from user.models import Opportunity

    product_label = rfq.product.name if rfq.product_id and rfq.product else ""
    opp_title = (product_label and f"Devis — {product_label}") or title
    ensure_opportunity(
        supplier,
        company=contact.company,
        contact=contact,
        title=f"{opp_title} #{rfq.pk}"[:255],
        source=Opportunity.Source.QUOTE_REQUEST,
        stage=Opportunity.Stage.REQUEST_RECEIVED,
    )
    return contact


def sync_from_sample_request(sample):
    """Boutique sample request → CRM contact + opportunity (« Demande reçue »)."""
    from user.models import Opportunity

    supplier = sample.supplier
    title = "Demande d'échantillon reçue"
    product_label = sample.product.name if sample.product_id and sample.product else ""
    body = "\n".join(
        part
        for part in [
            f"Produit : {product_label}" if product_label else "",
            f"Quantité : {sample.quantity} unités",
            f"Livraison : {sample.delivery_address}",
            (sample.message or "").strip(),
        ]
        if part
    )
    if sample.buyer_id:
        contact = sync_contact_from_buyer(
            supplier,
            buyer=sample.buyer,
            company_name=(sample.company or "").strip(),
            consent=True,
            interaction_title=title,
            interaction_body=body,
        )
    else:
        contact = sync_contact_from_buyer(
            supplier,
            guest_name=(sample.name or "").strip(),
            guest_email=(sample.email or "").strip(),
            guest_phone=(sample.phone or "").strip(),
            consent=True,
            interaction_title=title,
            interaction_body=body,
        )
    opp_title = (product_label and f"Échantillon — {product_label}") or "Demande d'échantillon"
    ensure_opportunity(
        supplier,
        company=contact.company,
        contact=contact,
        title=f"{opp_title} #{sample.pk}"[:255],
        source=Opportunity.Source.SAMPLE_REQUEST,
        stage=Opportunity.Stage.REQUEST_RECEIVED,
    )
    return contact


@transaction.atomic
def sync_from_order(order):
    """Catalog order → CRM contact + opportunity."""
    from user.models import Opportunity, Order

    supplier = order.supplier
    product_name = order.product.name if order.product_id else "Produit"
    title = f"Commande — {product_name}"
    body = f"Quantité : {order.quantity}"
    specs = (order.specifications or "").strip()
    if specs:
        body = f"{body}\n{specs}"

    if order.buyer_id:
        contact = sync_contact_from_buyer(
            supplier,
            buyer=order.buyer,
            consent=True,
            interaction_title=title,
            interaction_body=body,
        )
    else:
        guest_phone = ""
        if order.guest_phone:
            code = (order.guest_phone_country_code or "").strip()
            guest_phone = f"{code} {order.guest_phone}".strip()
        contact = sync_contact_from_buyer(
            supplier,
            guest_name=(order.guest_name or "Client invité").strip(),
            guest_phone=guest_phone,
            consent=True,
            interaction_title=title,
            interaction_body=body,
        )

    delivered = getattr(order, "status", "") == "delivered"
    won = getattr(order, "status", "") == Order.Status.WON
    stage = Opportunity.Stage.REQUEST_RECEIVED
    if delivered or won:
        stage = Opportunity.Stage.WON
    opportunity = ensure_opportunity(
        supplier,
        company=contact.company,
        contact=contact,
        title=f"Commande #{order.pk} — {product_name}"[:255],
        source=Opportunity.Source.ORDER,
        stage=stage,
    )
    _link_order_to_opportunity(order, opportunity)
    return contact


def _link_order_to_opportunity(order, opportunity):
    if opportunity is None or opportunity.order_id:
        return opportunity
    opportunity.order = order
    opportunity.save(update_fields=["order", "updated_at"])
    return opportunity


def resolve_opportunity_for_order(order):
    """Find the CRM deal tied to a catalogue order (by FK or title)."""
    from user.models import Opportunity

    supplier = order.supplier
    linked = (
        Opportunity.objects.filter(supplier=supplier, order_id=order.pk)
        .order_by("-updated_at")
        .first()
    )
    if linked:
        return linked
    needle = f"#{order.pk}"
    by_title = (
        Opportunity.objects.filter(supplier=supplier, title__contains=needle)
        .order_by("-updated_at")
        .first()
    )
    if by_title:
        return _link_order_to_opportunity(order, by_title)
    if order.quote_proposal_id:
        quote_opp = Opportunity.objects.filter(
            supplier=supplier, demand_id=order.quote_proposal.demand_id
        ).first()
        if quote_opp:
            return _link_order_to_opportunity(order, quote_opp)
    return None


@transaction.atomic
def sync_order_outcome(order, *, outcome: str, actor=None):
    """Reflect supplier won/lost on an order in the CRM pipeline."""
    from user.models import Opportunity

    sync_from_order(order)
    opportunity = resolve_opportunity_for_order(order)
    if opportunity is None and order.buyer_id:
        contact = sync_contact_from_buyer(
            order.supplier,
            buyer=order.buyer,
            consent=True,
        )
        product_name = order.product.name if order.product_id else "Produit"
        opportunity = ensure_opportunity(
            order.supplier,
            company=contact.company,
            contact=contact,
            title=f"Commande #{order.pk} — {product_name}"[:255],
            source=Opportunity.Source.ORDER,
            stage=Opportunity.Stage.NEGOTIATION,
        )
        _link_order_to_opportunity(order, opportunity)

    if opportunity is None:
        return None

    value = _order_amount(order)

    if outcome == "won":
        set_stage(
            opportunity,
            Opportunity.Stage.WON,
            actor=actor,
            note=f"Commande #{order.pk} marquée gagnée.",
        )
    else:
        set_stage(
            opportunity,
            Opportunity.Stage.LOST,
            actor=actor,
            lost_reason="Commande marquée perdue par le fournisseur.",
            note=f"Commande #{order.pk} marquée perdue.",
        )

    if value is not None:
        opportunity.estimated_value = value
        opportunity.save(update_fields=["estimated_value", "updated_at"])
    return opportunity


ORDER_STATUSES_WON = ("confirmed", "in_production", "shipped", "delivered", "completed", "won")


@transaction.atomic
def sync_order_execution(order, *, actor=None, note=""):
    """
    Keep the CRM deal in step with the order's execution status: a confirmed
    (or further along) order is a won deal worth the order amount, a cancelled
    one is lost, and a reopened one goes back to negotiation.
    """
    from user.models import Opportunity

    opportunity = resolve_opportunity_for_order(order)
    if opportunity is None:
        sync_from_order(order)
        opportunity = resolve_opportunity_for_order(order)
    if opportunity is None:
        return None

    status = order.status
    if status in ORDER_STATUSES_WON:
        if opportunity.stage != Opportunity.Stage.WON:
            set_stage(
                opportunity,
                Opportunity.Stage.WON,
                actor=actor,
                note=note or f"Commande #{order.pk} — {order.get_status_display()}.",
            )
        if not opportunity.estimated_value:
            amount = _order_amount(order)
            if amount:
                opportunity.estimated_value = amount
                opportunity.save(update_fields=["estimated_value", "updated_at"])
    elif status == "cancelled":
        if opportunity.stage != Opportunity.Stage.LOST:
            set_stage(
                opportunity,
                Opportunity.Stage.LOST,
                actor=actor,
                lost_reason=(note or f"Commande #{order.pk} annulée.")[:255],
                note=f"Commande #{order.pk} annulée.",
            )
    elif status == "pending" and opportunity.stage == Opportunity.Stage.LOST:
        set_stage(
            opportunity,
            Opportunity.Stage.NEGOTIATION,
            actor=actor,
            note=f"Commande #{order.pk} relancée.",
        )
        opportunity.closed_at = None
        opportunity.lost_reason = ""
        opportunity.save(update_fields=["closed_at", "lost_reason", "updated_at"])
    return opportunity


@transaction.atomic
def ensure_contact(supplier, company, *, buyer=None, consent=False, **defaults):
    from user.models import CrmContact

    if buyer is not None:
        existing = CrmContact.objects.filter(
            supplier=supplier, company=company, user=buyer
        ).first()
        if existing:
            changed = []
            if consent and not existing.contact_consent:
                existing.contact_consent = True
                existing.consent_updated_at = timezone.now()
                changed.extend(["contact_consent", "consent_updated_at"])
            if not existing.first_name and getattr(buyer, "first_name", ""):
                existing.first_name = buyer.first_name[:120]
                changed.append("first_name")
            if not existing.last_name and getattr(buyer, "last_name", ""):
                existing.last_name = buyer.last_name[:120]
                changed.append("last_name")
            if changed:
                existing.save(update_fields=[*changed, "updated_at"])
            return existing, False
        contact = CrmContact.objects.create(
            supplier=supplier,
            company=company,
            user=buyer,
            first_name=getattr(buyer, "first_name", "") or "",
            last_name=getattr(buyer, "last_name", "") or "",
            email=getattr(buyer, "email", "") or "",
            phone=getattr(buyer, "phone_number", "") or "",
            contact_consent=consent,
            consent_updated_at=timezone.now() if consent else None,
            **defaults,
        )
        return contact, True

    contact = CrmContact.objects.create(
        supplier=supplier, company=company, contact_consent=True, **defaults
    )
    return contact, True


@transaction.atomic
def ensure_opportunity(
    supplier,
    *,
    company,
    contact,
    title,
    source,
    stage,
    demand=None,
):
    from user.models import Opportunity

    if demand is not None:
        existing = Opportunity.objects.filter(supplier=supplier, demand=demand).first()
    else:
        existing = Opportunity.objects.filter(
            supplier=supplier, contact=contact, title=title
        ).first()
    if existing:
        return existing
    return Opportunity.objects.create(
        supplier=supplier,
        company=company,
        contact=contact,
        demand=demand,
        title=title[:255],
        source=source,
        stage=stage,
    )


def _record_prospect_signal(
    supplier,
    contact,
    *,
    title,
    body,
    source,
):
    """One CRM activity per contact/title/day + prospect-stage opportunity."""
    from user.models import CrmActivity, Opportunity

    title = (title or "Prospect")[:255]
    today = timezone.now().date()
    already = CrmActivity.objects.filter(
        supplier=supplier,
        contact=contact,
        kind=CrmActivity.Kind.SYSTEM,
        title=title,
        created_at__date=today,
    ).exists()
    if not already:
        record_activity(
            supplier,
            kind=CrmActivity.Kind.SYSTEM,
            title=title,
            body=(body or "")[:2000],
            company=contact.company,
            contact=contact,
        )
    ensure_opportunity(
        supplier,
        company=contact.company,
        contact=contact,
        title=title,
        source=source,
        stage=Opportunity.Stage.PROSPECT,
    )


def supplier_matches_category_intent(supplier, subcategory) -> bool:
    """Supplier is relevant when they sell in the subcategory or declared interest in it."""
    from user.models import Product

    if Product.objects.filter(
        supplier=supplier, is_active=True, subcategory=subcategory
    ).exists():
        return True
    return supplier.interested_subcategories.filter(pk=subcategory.pk).exists()


@transaction.atomic
def sync_from_category_intent(supplier, buyer, subcategory, *, via="search_filter"):
    """
    Qualified buyer intent: category + subcategory filter or saved buyer interest.
    Coordinates are shared with the supplier (consent=True) without Pak-dating.
    """
    from user.models import Opportunity

    sub_label = (subcategory.name_fr or subcategory.name_en or "Sous-catégorie").strip()
    parent = subcategory.parent
    parent_label = ""
    if parent:
        parent_label = (parent.name_fr or parent.name_en or "").strip()

    if via == "buyer_interest":
        title = f"Intérêt déclaré — {sub_label}"[:255]
        body = (
            f"L'acheteur a enregistré la sous-catégorie « {sub_label} » "
            "dans ses centres d'intérêt."
        )
    else:
        title = f"Recherche ciblée — {sub_label}"[:255]
        if parent_label:
            body = (
                f"L'acheteur a filtré {parent_label} / {sub_label} "
                "et votre catalogue correspond."
            )
        else:
            body = (
                f"L'acheteur a filtré la sous-catégorie « {sub_label} » "
                "et votre catalogue correspond."
            )

    contact = sync_contact_from_buyer(
        supplier,
        buyer=buyer,
        consent=True,
        interaction_title=title,
        interaction_body=body,
    )
    _record_prospect_signal(
        supplier,
        contact,
        title=title,
        body=body,
        source=Opportunity.Source.CATEGORY_INTENT,
    )
    return contact


def sync_category_intent_for_search_results(buyer, supplier_ids, subcategory):
    """CRM sync when listing results match a category + subcategory filter."""
    from user.models import Supplier

    if buyer is None or getattr(buyer, "supplier_id", None):
        return
    seen: set[int] = set()
    for raw_id in supplier_ids:
        supplier_id = int(raw_id)
        if supplier_id in seen:
            continue
        seen.add(supplier_id)
        supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
        if supplier is None:
            continue
        if not supplier_matches_category_intent(supplier, subcategory):
            continue
        try:
            sync_from_category_intent(
                supplier, buyer, subcategory, via="search_filter"
            )
        except Exception:
            logger.exception(
                "CRM category intent failed supplier=%s buyer=%s sub=%s",
                supplier_id,
                buyer.pk,
                subcategory.pk,
            )


def sync_suppliers_for_buyer_interest_subcategory(buyer, subcategory):
    """Notify CRM for all suppliers matching a newly saved buyer interest."""
    from django.db.models import Q

    from user.models import Supplier

    if buyer is None or getattr(buyer, "supplier_id", None):
        return
    suppliers = (
        Supplier.objects.filter(is_active=True)
        .filter(
            Q(products__is_active=True, products__subcategory=subcategory)
            | Q(interested_subcategories=subcategory)
        )
        .distinct()
    )
    for supplier in suppliers:
        try:
            sync_from_category_intent(
                supplier, buyer, subcategory, via="buyer_interest"
            )
        except Exception:
            logger.exception(
                "CRM buyer interest sync failed supplier=%s buyer=%s sub=%s",
                supplier.pk,
                buyer.pk,
                subcategory.pk,
            )


@transaction.atomic
def sync_from_boutique_visit(supplier, buyer, *, source="direct"):
    """Logged-in buyer opened the supplier storefront."""
    from user.models import Opportunity

    source = (source or "direct").strip()
    contact = sync_contact_from_buyer(
        supplier,
        buyer=buyer,
        consent=False,
    )
    _record_prospect_signal(
        supplier,
        contact,
        title="Visite boutique",
        body=f"L'acheteur a visité votre boutique (source : {source}).",
        source=Opportunity.Source.STORE_VISIT,
    )
    return contact


@transaction.atomic
def sync_from_product_view(supplier, buyer, product, *, source="direct"):
    """Logged-in buyer opened a product on the supplier storefront."""
    from user.models import Opportunity

    product_name = getattr(product, "name", "") or "Produit"
    source = (source or "direct").strip()
    contact = sync_contact_from_buyer(
        supplier,
        buyer=buyer,
        consent=False,
    )
    title = f"Consultation — {product_name}"[:255]
    _record_prospect_signal(
        supplier,
        contact,
        title=title,
        body=(
            f"L'acheteur a consulté {product_name} sur votre boutique "
            f"(source : {source})."
        ),
        source=Opportunity.Source.PRODUCT_VIEW,
    )
    return contact


@transaction.atomic
def sync_from_favorite(supplier, buyer, *, product=None):
    """Logged-in buyer favorited the supplier or one of its products."""
    from user.models import Opportunity

    if product is not None:
        product_name = getattr(product, "name", "") or "Produit"
        title = f"Favori — {product_name}"[:255]
        body = f"L'acheteur a ajouté {product_name} aux favoris."
    else:
        title = "Favori boutique"
        body = "L'acheteur a ajouté votre entreprise aux favoris."
    contact = sync_contact_from_buyer(
        supplier,
        buyer=buyer,
        consent=False,
        interaction_title=title,
        interaction_body=body,
    )
    ensure_opportunity(
        supplier,
        company=contact.company,
        contact=contact,
        title=title,
        source=Opportunity.Source.FAVORITE,
        stage=Opportunity.Stage.PROSPECT,
    )
    return contact


# Backfills run inside migrations 0066/0075 with the live Order model: columns
# added by later migrations do not exist yet at that point and must be deferred.
ORDER_FIELDS_ADDED_AFTER_BACKFILL = (
    "quote_proposal",
    "quote_response",
    "commercial_snapshot",
    "buyer_po_reference",
    "billing_address",
    "delivery_address",
    "delivery_instructions",
    "receiver_contact",
    "expected_delivery_date",
    "execution_notes",
    "origin",
    "trade_scope",
    "delivery_wilaya",
    "destination_country",
    "destination_city",
    "incoterm",
    "buyer_note",
    "supplier_archived_at",
)


def fields_missing_from_db(model, prefix=""):
    """Live-model fields whose column does not exist yet (backfills inside old migrations)."""
    from django.db import connection

    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, model._meta.db_table
            )
        }
    return [
        f"{prefix}{field.name}"
        for field in model._meta.concrete_fields
        if field.column not in columns
    ]


def _deferred_for_commerce(model):
    from user.models import Product, Supplier, User

    return {
        *fields_missing_from_db(model),
        *fields_missing_from_db(Supplier, "supplier__"),
        *fields_missing_from_db(Product, "product__"),
        *fields_missing_from_db(User, "buyer__"),
    }


def order_backfill_deferred_fields():
    """Fields to defer when an old migration loads orders with their related rows."""
    from user.models import Order

    return set(ORDER_FIELDS_ADDED_AFTER_BACKFILL) | _deferred_for_commerce(Order)


def backfill_from_existing_commerce():
    """Replay orders and boutique RFQs into the CRM. Idempotent."""
    from user.models import Order, RFQRequest

    synced = {"orders": 0, "rfqs": 0, "errors": 0}
    for order in (
        Order.objects.select_related("buyer", "supplier", "product")
        .defer(*order_backfill_deferred_fields())
        .all()
    ):
        try:
            sync_from_order(order)
            synced["orders"] += 1
        except Exception:
            synced["errors"] += 1
            logger.exception("CRM backfill failed for order %s", order.pk)
    for rfq in (
        RFQRequest.objects.select_related("buyer", "supplier", "product")
        .defer(*_deferred_for_commerce(RFQRequest))
        .all()
    ):
        try:
            sync_from_rfq(rfq)
            synced["rfqs"] += 1
        except Exception:
            synced["errors"] += 1
            logger.exception("CRM backfill failed for RFQ %s", rfq.pk)
    return synced


@transaction.atomic
def record_activity(
    supplier,
    *,
    kind,
    title,
    body="",
    opportunity=None,
    company=None,
    contact=None,
    due_at=None,
    created_by=None,
):
    from user.models import CrmActivity, CrmCompany

    activity = CrmActivity.objects.create(
        supplier=supplier,
        opportunity=opportunity,
        company=company or (opportunity.company if opportunity else None),
        contact=contact or (opportunity.contact if opportunity else None),
        kind=kind,
        title=title[:255],
        body=body,
        due_at=due_at,
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )
    target = activity.company
    if target is not None:
        CrmCompany.objects.filter(pk=target.pk).update(
            last_activity_at=timezone.now()
        )
    return activity


def _order_amount(order):
    """Agreed order total, else quantity × unit price (None when unknown)."""
    from user.commission import reference_amount_for

    amount, _currency = reference_amount_for(order)
    return amount


@transaction.atomic
def set_stage(opportunity, stage, *, actor=None, lost_reason="", note=""):
    """Move an opportunity and keep the full history as an activity."""
    from user.models import CrmActivity, Opportunity

    previous = opportunity.stage
    if previous == stage and not lost_reason:
        return opportunity

    opportunity.stage = stage
    fields = ["stage", "updated_at"]
    if stage in (Opportunity.Stage.WON, Opportunity.Stage.LOST):
        opportunity.closed_at = timezone.now()
        fields.append("closed_at")
    if stage == Opportunity.Stage.LOST and lost_reason:
        opportunity.lost_reason = lost_reason[:255]
        fields.append("lost_reason")
    if stage == Opportunity.Stage.WON:
        opportunity.probability = 100
        fields.append("probability")
        if not opportunity.estimated_value and opportunity.order_id:
            amount = _order_amount(opportunity.order)
            if amount:
                opportunity.estimated_value = amount
                fields.append("estimated_value")
    opportunity.save(update_fields=fields)

    labels = dict(Opportunity.Stage.choices)
    record_activity(
        opportunity.supplier,
        kind=CrmActivity.Kind.STAGE_CHANGE,
        title=f"{labels.get(previous, previous)} → {labels.get(stage, stage)}",
        body=note or lost_reason,
        opportunity=opportunity,
        created_by=actor,
    )
    return opportunity


@transaction.atomic
def schedule_followups(opportunity, *, created_by=None):
    """Create follow-up tasks at the configured delays (24 h and 48 h).

    These are internal reminders only. Nothing is sent to the buyer: no
    WhatsApp, no email, no notification without an explicit consent and a real
    configured integration.
    """
    from user.models import CrmActivity

    now = timezone.now()
    created = []
    for hours in get_int_list("followup_reminder_hours") or [24, 48]:
        due = now + timedelta(hours=hours)
        exists = CrmActivity.objects.filter(
            supplier=opportunity.supplier,
            opportunity=opportunity,
            kind=CrmActivity.Kind.REMINDER,
            due_at=due,
        ).exists()
        if exists:
            continue
        created.append(
            record_activity(
                opportunity.supplier,
                kind=CrmActivity.Kind.REMINDER,
                title=f"Relancer sous {hours} h",
                body="Relance interne : vérifier la réponse de l'acheteur.",
                opportunity=opportunity,
                due_at=due,
                created_by=created_by,
            )
        )
    return created


@transaction.atomic
def sync_from_quote_demand(demand, supplier, *, source=None, consent=None):
    """Create or update entreprise + contact + opportunité + activité.

    Called whenever a supplier gains access to a demand — private request,
    selection on a public request, or paid unlock.
    """
    from user.models import CrmActivity, Opportunity

    buyer = demand.buyer
    is_addressed = demand.kind == "private" or _is_selected(demand, supplier)
    company, _ = ensure_company(supplier, buyer=buyer)
    contact, _ = ensure_contact(
        supplier,
        company,
        buyer=buyer,
        # A buyer who addresses a supplier directly has authorised the contact.
        consent=is_addressed if consent is None else consent,
    )

    opportunity = Opportunity.objects.filter(
        supplier=supplier, demand=demand
    ).first()
    if opportunity is None:
        opportunity = Opportunity.objects.create(
            supplier=supplier,
            company=company,
            contact=contact,
            demand=demand,
            title=demand_title(demand),
            source=source or Opportunity.Source.QUOTE_REQUEST,
            stage=Opportunity.Stage.REQUEST_RECEIVED,
            estimated_value=demand.indicative_budget,
        )
        record_activity(
            supplier,
            kind=CrmActivity.Kind.SYSTEM,
            title="Demande de devis reçue",
            body=(demand.description or "")[:2000],
            opportunity=opportunity,
            created_by=None,
        )
        schedule_followups(opportunity)
    elif opportunity.stage == Opportunity.Stage.PROSPECT:
        set_stage(opportunity, Opportunity.Stage.REQUEST_RECEIVED)

    return opportunity


def demand_title(demand) -> str:
    label = ""
    if demand.subcategory_id:
        label = getattr(demand.subcategory, "name_fr", "") or demand.subcategory.name_en
    elif demand.category_id:
        label = getattr(demand.category, "name_fr", "") or demand.category.name_en
    if label:
        return f"{label} — {demand.quantity} u."[:255]
    return f"Demande #{demand.pk}"


def _is_selected(demand, supplier) -> bool:
    try:
        return demand.selections.filter(supplier=supplier).exists()
    except Exception:
        return False


STAGES_BEFORE_QUOTE_SENT = frozenset(
    ("prospect", "request_received")
)


@transaction.atomic
def mark_proposal_sent(
    supplier,
    *,
    buyer=None,
    order=None,
    rfq=None,
    actor=None,
    note="Proposition commerciale envoyée.",
    estimated_value=None,
):
    """Move the matching open opportunity to « Devis envoyé »."""
    from user.models import Opportunity

    contact = None
    needle = ""
    if order is not None:
        contact = sync_from_order(order)
        needle = f"#{order.pk}"
    elif rfq is not None:
        contact = sync_from_rfq(rfq)
        needle = f"#{rfq.pk}"
    elif buyer is not None:
        contact = sync_contact_from_buyer(
            supplier,
            buyer=buyer,
            consent=True,
        )

    if contact is None:
        return None

    open_opps = Opportunity.objects.filter(
        supplier=supplier, contact=contact
    ).exclude(stage__in=(Opportunity.Stage.WON, Opportunity.Stage.LOST))
    target = None
    if needle:
        target = open_opps.filter(title__contains=needle).first()
    if target is None:
        target = open_opps.order_by("-updated_at").first()
    if target is None:
        source = (
            Opportunity.Source.QUOTE_REQUEST
            if rfq is not None
            else Opportunity.Source.ORDER
        )
        target = ensure_opportunity(
            supplier,
            company=contact.company,
            contact=contact,
            title=note[:255],
            source=source,
            stage=Opportunity.Stage.QUOTE_SENT,
        )

    if target.stage in STAGES_BEFORE_QUOTE_SENT:
        set_stage(
            target,
            Opportunity.Stage.QUOTE_SENT,
            actor=actor,
            note=note,
        )
    if estimated_value is not None:
        target.estimated_value = estimated_value
        target.save(update_fields=["estimated_value", "updated_at"])
    return target


def backfill_proposals_sent():
    """Replay already-sent boutique proposals into the pipeline."""
    from user.models import OrderResponse, QuoteResponse

    count = 0
    for response in QuoteResponse.objects.select_related(
        "quote_request", "quote_request__buyer", "quote_request__product", "supplier"
    ).all():
        try:
            mark_proposal_sent(
                response.supplier,
                rfq=response.quote_request,
                estimated_value=response.proposed_price,
                note=f"Proposition devis #{response.pk} envoyée.",
            )
            count += 1
        except Exception:
            logger.exception("CRM quote-sent backfill failed for RFQ response %s", response.pk)
    for response in OrderResponse.objects.select_related(
        "order", "order__buyer", "order__product", "supplier"
    ).all():
        try:
            mark_proposal_sent(
                response.supplier,
                order=response.order,
                estimated_value=response.proposed_price,
                note=f"Proposition commande #{response.pk} envoyée.",
            )
            count += 1
        except Exception:
            logger.exception("CRM quote-sent backfill failed for order response %s", response.pk)
    return count


def opportunity_for_demand(demand, supplier):
    from user.models import Opportunity

    return Opportunity.objects.filter(supplier=supplier, demand=demand).first()


def opportunity_started_at(opp):
    """First date of the operation: the earliest of the deal, its order and its quote request."""
    dates = [opp.created_at]
    if opp.order_id and opp.order is not None:
        dates.append(opp.order.created_at)
    if opp.demand_id and opp.demand is not None:
        dates.append(opp.demand.created_at)
    return min(d for d in dates if d is not None)


def opportunities_started_between(supplier, start=None, end=None) -> list:
    """Deals whose operation began in [start, end), most recently updated first."""
    qs = (
        opportunities_for(supplier)
        .select_related("order", "demand", "company", "contact")
        .order_by("-updated_at")
    )
    if start is not None:
        # The first date is never later than created_at, so this only trims rows
        # that started before the window anyway.
        qs = qs.filter(created_at__gte=start)
    rows = []
    for opp in qs:
        started = opportunity_started_at(opp)
        if start is not None and started < start:
            continue
        if end is not None and started >= end:
            continue
        rows.append((opp, started))
    return rows


def opportunity_value(opp):
    """Estimated value, else the linked order amount (None when unknown)."""
    if opp.estimated_value:
        return opp.estimated_value
    if opp.order_id and opp.order is not None:
        return _order_amount(opp.order)
    return None


def pipeline_rows(supplier, start=None, end=None) -> list:
    """``(opportunity, started_at)`` shown in the pipeline for the period."""
    return opportunities_started_between(supplier, start, end)[:500]


def split_open_and_won(rows) -> tuple[list, list]:
    """Open deals (potential stages) and won deals among pipeline ``rows``."""
    open_opps = [opp for opp, _ in rows if opp.stage in POTENTIAL_STAGES]
    won_opps = [opp for opp, _ in rows if opp.stage == "won"]
    return open_opps, won_opps


def pipeline_payload(supplier, start=None, end=None, rows=None) -> list[dict]:
    """Kanban columns with totals, ordered by the official pipeline."""
    from user.models import Opportunity

    if rows is None:
        rows = pipeline_rows(supplier, start, end)
    grouped: dict[str, list] = {code: [] for code, _ in Opportunity.Stage.choices}
    for opp, started in rows:
        grouped.setdefault(opp.stage, []).append((opp, started))

    columns = []
    for code, label in Opportunity.Stage.choices:
        items = grouped.get(code, [])
        total = sum((opportunity_value(opp) or 0) for opp, _ in items)
        columns.append(
            {
                "stage": code,
                "label": label,
                "count": len(items),
                "totalValue": str(total),
                "items": [
                    {**opportunity_payload(opp), "startedAt": started}
                    for opp, started in items
                ],
            }
        )
    return columns


POTENTIAL_STAGES = ("request_received", "quote_sent", "negotiation")


def _roi(margin, cost):
    return round(float(margin / cost * 100), 1) if cost > 0 else None


def _known_amount(opp):
    value = opportunity_value(opp)
    if value is None:
        return None
    value = Decimal(str(value))
    return value if value > 0 else None


def _margin_block(opps, cost) -> dict | None:
    """Gross margin and ROI (%) of ``opps`` against ``cost`` (DA).

    None without operations. Operations without an amount are counted in
    ``missingAmount``; when none has an amount, amount/margin/ROI stay None.
    """
    if not opps:
        return None
    amounts = [a for a in (_known_amount(opp) for opp in opps) if a is not None]
    block = {
        "amount": None,
        "margin": None,
        "roi": None,
        "count": len(opps),
        "missingAmount": len(opps) - len(amounts),
    }
    if amounts:
        amount = sum(amounts, Decimal("0"))
        margin = amount - cost
        block.update(amount=str(amount), margin=str(margin), roi=_roi(margin, cost))
    return block


def margin_details(won_opps, open_opps, cost) -> list[dict]:
    """Partial margin / ROI per operation.

    Pragmadinar spends are not tied to an operation, so ``cost`` is split in equal
    shares between the operations that have an amount: the partial margins add up
    to the cumulative margin. Operations without an amount get no share.
    """
    rows = [(opp, "real") for opp in won_opps] + [(opp, "potential") for opp in open_opps]
    with_amount = [opp for opp, _ in rows if _known_amount(opp) is not None]
    share = (
        (cost / len(with_amount)).quantize(Decimal("0.01"))
        if with_amount
        else Decimal("0")
    )
    details = []
    for opp, kind in rows:
        amount = _known_amount(opp)
        margin = amount - share if amount is not None else None
        details.append(
            {
                "id": opp.id,
                "title": opp.title,
                "stage": opp.stage,
                "stageLabel": opp.get_stage_display(),
                "kind": kind,
                "orderId": opp.order_id,
                "demandId": opp.demand_id,
                "companyName": opp.company.name if opp.company_id else "",
                "amount": str(amount) if amount is not None else None,
                "allocatedCost": str(share) if amount is not None else None,
                "margin": str(margin) if margin is not None else None,
                "roi": _roi(margin, share) if margin is not None else None,
            }
        )
    return details


def profitability_payload(supplier, period, won_opps, open_opps) -> dict:
    """Won / open operations vs Pragmadinar consumed on the same period.

    ``potential`` = open deals only, ``real`` = won deals, ``cumulative`` = both.
    ``potential`` and ``cumulative`` carry a per-operation breakdown; ``cumulative``
    needs an amount on both sides.
    """
    from user.platform_config import pragmadinar_to_dzd
    from user.pragmadinar import format_pragmadinar_amount
    from user.pragmadinar_consumption import consumed_total

    consumed = consumed_total(supplier, period.start, period.end)
    consumed_dzd = pragmadinar_to_dzd(consumed)
    won_opps = list(won_opps)
    open_opps = list(open_opps)
    potential = _margin_block(open_opps, consumed_dzd)
    if potential:
        potential["details"] = margin_details([], open_opps, consumed_dzd)
    real = _margin_block(won_opps, consumed_dzd)
    cumulative = None
    if potential and real and potential["amount"] and real["amount"]:
        cumulative = _margin_block(won_opps + open_opps, consumed_dzd)
        cumulative["details"] = margin_details(won_opps, open_opps, consumed_dzd)
    return {
        "consumed": format_pragmadinar_amount(consumed),
        "consumedDzd": str(consumed_dzd),
        "allocation": "equal_share",
        "potential": potential,
        "real": real,
        "cumulative": cumulative,
    }


def opportunity_payload(opp) -> dict:
    return {
        "id": opp.id,
        "title": opp.title,
        "stage": opp.stage,
        "stageLabel": opp.get_stage_display(),
        "source": opp.source,
        "sourceLabel": opp.get_source_display(),
        "companyId": opp.company_id,
        "companyName": opp.company.name if opp.company_id else "",
        "contactId": opp.contact_id,
        "contactName": (
            f"{opp.contact.first_name} {opp.contact.last_name}".strip()
            if opp.contact_id
            else ""
        ),
        "demandId": opp.demand_id,
        "orderId": opp.order_id,
        "estimatedValue": str(value) if (value := opportunity_value(opp)) else "",
        "probability": opp.probability,
        "nextAction": opp.next_action,
        "nextActionAt": opp.next_action_at,
        "lostReason": opp.lost_reason,
        "closedAt": opp.closed_at,
        "createdAt": opp.created_at,
        "updatedAt": opp.updated_at,
    }


def company_payload(company) -> dict:
    open_opps = company.opportunities.exclude(stage__in=["won", "lost"])
    latest = open_opps.order_by("-updated_at").first()
    return {
        "id": company.id,
        "name": company.name,
        "sector": company.sector,
        "wilaya": company.wilaya,
        "size": company.size,
        "status": company.status,
        "statusLabel": company.get_status_display(),
        "website": company.website or "",
        "notes": company.notes,
        "isPlatformBuyer": company.buyer_id is not None,
        "contactCount": company.contacts.filter(is_archived=False).count(),
        "openOpportunities": open_opps.count(),
        "latestStage": latest.stage if latest else "",
        "latestStageLabel": latest.get_stage_display() if latest else "",
        "lastActivityAt": company.last_activity_at,
        "createdAt": company.created_at,
    }


def activity_payload(activity) -> dict:
    contact_name = ""
    if activity.contact_id:
        contact_name = (
            f"{activity.contact.first_name} {activity.contact.last_name}".strip()
            or activity.contact.email
        )
    return {
        "id": activity.id,
        "kind": activity.kind,
        "kindLabel": activity.get_kind_display(),
        "title": activity.title,
        "body": activity.body,
        "dueAt": activity.due_at,
        "doneAt": activity.done_at,
        "opportunityId": activity.opportunity_id,
        "companyId": activity.company_id,
        "companyName": activity.company.name if activity.company_id else "",
        "contactId": activity.contact_id,
        "contactName": contact_name,
        "createdAt": activity.created_at,
        "isOverdue": bool(
            activity.due_at and not activity.done_at and activity.due_at < timezone.now()
        ),
    }
