"""
API for quote requests and supplier responses (devis).
"""

import json
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import crm, rbac
from user.api.serializers import BuyerOrderSerializer
from user.commission import register_order_commission
from user.export_terms import TRADE_EXPORT, clean_export_country, clean_trade_scope
from user.models import (
    AppNotification,
    Category,
    Event,
    Message,
    Opportunity,
    Order,
    PragmadinarLedger,
    PragmadinarRechargeRequest,
    PragmadinarService,
    PaknomikPlan,
    Product,
    QuoteDemand,
    QuoteDemandEngagement,
    QuoteDemandPhoto,
    QuoteDemandSelection,
    QuoteProposal,
    QuoteProposalEvent,
    Supplier,
    TradeScope,
)

logger = logging.getLogger(__name__)
from user.order_workflow import record_status_event
from user.pragmadinar import (
    CATALOG_SERVICES,
    LISTING_CLASS_LABELS,
    LISTING_CLASS_ORDER,
    OPPORTUNITY_SERVICE_CODE,
    PUBLIC_QUOTE_ENGAGED_CAP,
    QUOTE_PUBLIC_COOLDOWN_HOURS,
    UNBILLED_SERVICE_CODES,
    WELCOME_SPLIT_PERCENT,
    access_label,
    format_cost_label,
    format_pragmadinar_amount,
    get_supplier_plan_code,
    opportunity_unlock_cost,
    on_price_list,
    seed_pragmadinar_catalog,
    tariff_display_cost,
    wallet_payload,
)
from user.quote_market import (
    PUBLIC_MARKET_DAYS,
    buyer_can_relaunch,
    expire_stale_public_market_listings,
    initial_market_fields,
)
from user.proposal_commerce import (
    DEFAULT_CURRENCY,
    compute_totals,
    freight_total,
    legacy_line_from_scalar,
    parse_commercial_terms,
    parse_line_items,
    proposal_snapshot,
)
from user.events import log_buyer_event, log_system_event
from user.photo_uploads import clean_photos, sanitized_photos, save_photos, strip_metadata
from user.platform_config import pragmadinar_rate_dzd, supplier_welcome_pragmadinar
from user.scoring import apply_score_delta

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _require_supplier(request):
    if not request.user.supplier_id:
        raise PermissionDenied({"error": "Compte fournisseur requis."})
    return request.user.supplier


def _media_url(request, file_field):
    if not file_field:
        return None
    url = file_field.url
    if request:
        return request.build_absolute_uri(url)
    return url


def _category_name(category):
    return category.name_fr or category.name_en


def _notify(user, kind, title, body, link, demand=None):
    return AppNotification.objects.create(
        user=user,
        kind=kind,
        title=title,
        body=body,
        link=link,
        demand=demand,
    )


def _close_if_cap_reached(demand):
    if (
        demand.kind == QuoteDemand.Kind.PUBLIC
        and demand.status == QuoteDemand.Status.OPEN
        and demand.engagements.count() >= PUBLIC_QUOTE_ENGAGED_CAP
    ):
        demand.status = QuoteDemand.Status.CLOSED
        demand.save(update_fields=["status", "updated_at"])


def _mark_engaged(demand, supplier, source, *, paid_amount=0, ledger=None):
    engagement, created = QuoteDemandEngagement.objects.get_or_create(
        demand=demand,
        supplier=supplier,
        defaults={
            "source": source,
            "paid_amount": paid_amount,
            "ledger": ledger,
        },
    )
    if created:
        _close_if_cap_reached(demand)
        # Joining a public demand is a lead, not an authorised contact: the
        # buyer's coordinates stay masked until consent.
        crm.sync_from_quote_demand(
            demand,
            supplier,
            consent=source == QuoteDemandEngagement.Source.SELECTED_REPLIED,
        )
    return engagement, created


def _supplier_category_ids(supplier):
    category_ids = set(supplier.interested_categories.values_list("id", flat=True))
    subcategory_ids = set(supplier.interested_subcategories.values_list("id", flat=True))
    for cat_id, sub_id in supplier.products.filter(is_active=True).values_list(
        "category_id", "subcategory_id"
    ):
        if cat_id:
            category_ids.add(cat_id)
        if sub_id:
            subcategory_ids.add(sub_id)
    return category_ids, subcategory_ids


def _supplier_matches_demand_category(demand, supplier):
    category_ids, subcategory_ids = _supplier_category_ids(supplier)
    return demand.subcategory_id in subcategory_ids or demand.category_id in category_ids


def _is_selected(demand, supplier_id):
    return demand.selections.filter(supplier_id=supplier_id).exists()


def _is_engaged(demand, supplier_id):
    return demand.engagements.filter(supplier_id=supplier_id).exists()


QUOTE_WORKFLOW_LABELS = {
    "sent": "Envoyée",
    "waiting": "En attente",
    "revision": "Révision demandée",
    "won": "Gagnée",
    "lost": "Perdue",
    "expired": "Expirée",
}


def _supplier_has_contact_access(demand, supplier):
    return _is_selected(demand, supplier.id) or _is_engaged(demand, supplier.id)


def _buyer_contact_payload(demand, request, supplier):
    from user.api.serializers import user_avatar_url

    buyer = demand.buyer
    company = (
        buyer.company_name or buyer.get_full_name() or buyer.username or "—"
    )
    if supplier and not _supplier_has_contact_access(demand, supplier):
        return {
            "contactName": company,
            "contactPhone": "—",
            "contactEmail": "—",
            "contactLocation": demand.wilaya or getattr(buyer, "wilaya", "") or "—",
            "contactAvatar": None,
        }
    return {
        "contactName": buyer.get_full_name() or company,
        "contactPhone": getattr(buyer, "phone_number", None) or "—",
        "contactEmail": buyer.email or "—",
        "contactLocation": (
            demand.wilaya
            or getattr(buyer, "wilaya", "")
            or getattr(buyer, "address", "")
            or "—"
        ),
        "contactAvatar": user_avatar_url(request, buyer),
    }


def _supplier_workflow_status(demand, supplier, my_proposal):
    if my_proposal:
        proposal_status = my_proposal["status"]
        if proposal_status == QuoteProposal.Status.WON:
            return "won"
        if proposal_status in (QuoteProposal.Status.LOST, QuoteProposal.Status.EXPIRED):
            return "lost"
        if proposal_status == QuoteProposal.Status.DRAFT:
            return "sent"
        if proposal_status == QuoteProposal.Status.REVISION_REQUESTED:
            return "revision"
        if proposal_status == QuoteProposal.Status.SENT:
            if demand.status == QuoteDemand.Status.OPEN:
                return "waiting"
            return "lost"
    if demand.status == QuoteDemand.Status.RESOLVED:
        return "lost"
    if demand.status == QuoteDemand.Status.CLOSED:
        return "expired"
    return "sent"


def _can_respond_opportunity(demand, supplier):
    if demand.kind != QuoteDemand.Kind.PUBLIC:
        return False
    if demand.status != QuoteDemand.Status.OPEN:
        return False
    if not demand.visible_on_market or demand.admin_queue:
        return False
    if _is_selected(demand, supplier.id) or _is_engaged(demand, supplier.id):
        return False
    if demand.engagements.count() >= PUBLIC_QUOTE_ENGAGED_CAP:
        return False
    if demand.proposals.filter(supplier=supplier).exists():
        return False
    return _supplier_matches_demand_category(demand, supplier)


AUDITED_PROPOSAL_ACTIONS = {
    QuoteProposalEvent.Action.SENT,
    QuoteProposalEvent.Action.REVISED,
    QuoteProposalEvent.Action.REVISION_REQUESTED,
    QuoteProposalEvent.Action.ACCEPTED,
    QuoteProposalEvent.Action.REFUSED,
    QuoteProposalEvent.Action.CONVERTED,
}


def _log_proposal_event(proposal, action, actor, *, comment=""):
    event = QuoteProposalEvent.objects.create(
        proposal=proposal,
        version=proposal.version or 1,
        action=action,
        actor=actor,
        comment=(comment or "").strip(),
        snapshot=proposal_snapshot(proposal),
    )
    if action in AUDITED_PROPOSAL_ACTIONS:
        rbac.log_audit(
            actor,
            f"proposal.{action}",
            entity_type="quote_proposal",
            entity_id=proposal.id,
            new_value={
                "demandId": proposal.demand_id,
                "supplierId": proposal.supplier_id,
                "version": proposal.version or 1,
                "tradeScope": (proposal.commercial_terms or {}).get("tradeScope", "local"),
            },
            reason=(comment or "")[:500],
        )
    return event


def _parse_currency(value) -> str:
    code = str(value or "").strip().upper()
    if not code:
        return DEFAULT_CURRENCY
    if len(code) != 3 or not code.isalpha():
        raise ValidationError(
            {"error": "Devise invalide (code ISO à 3 lettres).", "code": "invalid_currency"}
        )
    return code


def _serialize_proposal_events(proposal, *, include_drafts=True):
    labels = dict(QuoteProposalEvent.Action.choices)
    events = proposal.events.all()
    if not include_drafts:
        events = [e for e in events if e.action != QuoteProposalEvent.Action.DRAFT_SAVED]
    return [
        {
            "id": event.id,
            "version": event.version,
            "action": event.action,
            "label": labels.get(event.action, event.action),
            "comment": event.comment or "",
            "createdAt": event.created_at.isoformat(),
        }
        for event in events
    ]


def _proposal_editable(proposal):
    return proposal.status in (
        QuoteProposal.Status.DRAFT,
        QuoteProposal.Status.REVISION_REQUESTED,
    )


def _supplier_can_respond(demand, supplier):
    if demand.status == QuoteDemand.Status.RESOLVED:
        return False
    existing = demand.proposals.filter(supplier=supplier).first()
    if existing:
        return _proposal_editable(existing)
    if _is_selected(demand, supplier.id) or _is_engaged(demand, supplier.id):
        return True
    if (
        demand.kind == QuoteDemand.Kind.PUBLIC
        and demand.status == QuoteDemand.Status.OPEN
        and demand.visible_on_market
        and not demand.admin_queue
        and demand.engagements.count() < PUBLIC_QUOTE_ENGAGED_CAP
    ):
        return _supplier_matches_demand_category(demand, supplier)
    return False


def _serialize_supplier_brief(supplier):
    score = getattr(supplier, "score_record", None)
    return {
        "id": supplier.id,
        "companyName": supplier.company_name,
        "wilaya": supplier.wilaya or "",
        "paknomikScore": score.score_total if score else 50,
        "logo": supplier.logo.url if supplier.logo else None,
    }


def _serialize_proposal(proposal, include_supplier=True):
    totals = {
        "subtotal": str(proposal.subtotal) if proposal.subtotal is not None else None,
        "discountTotal": (
            str(proposal.discount_total) if proposal.discount_total is not None else None
        ),
        "extraFees": str(proposal.extra_fees) if proposal.extra_fees is not None else None,
        "taxTotal": str(proposal.tax_total) if proposal.tax_total is not None else None,
        "totalAmount": (
            str(proposal.total_amount) if proposal.total_amount is not None else None
        ),
        "freightTotal": freight_total(proposal.commercial_terms),
    }
    data = {
        "id": proposal.id,
        "unitPrice": str(proposal.unit_price),
        "confirmedQuantity": proposal.confirmed_quantity,
        "leadTimeDays": proposal.lead_time_days,
        "moq": proposal.moq,
        "message": proposal.message,
        "status": proposal.status,
        "currency": proposal.currency or DEFAULT_CURRENCY,
        "lineItems": list(proposal.line_items or []),
        "commercialTerms": dict(proposal.commercial_terms or {}),
        "totals": totals,
        "version": proposal.version,
        "sentAt": proposal.sent_at.isoformat() if proposal.sent_at else None,
        "acceptedAt": proposal.accepted_at.isoformat() if proposal.accepted_at else None,
        "buyerResponseComment": proposal.buyer_response_comment or "",
        "refusedAt": proposal.refused_at.isoformat() if proposal.refused_at else None,
        "createdAt": proposal.created_at.isoformat(),
        # The buyer-facing payload (with supplier brief) never exposes draft saves.
        "history": _serialize_proposal_events(proposal, include_drafts=not include_supplier),
    }
    if include_supplier:
        data["supplier"] = _serialize_supplier_brief(proposal.supplier)
    converted = getattr(proposal, "converted_order", None)
    data["orderId"] = converted.pk if converted else None
    return data


def _serialize_demand(demand, request, *, viewer="buyer", supplier=None):
    selected_ids = list(demand.selections.values_list("supplier_id", flat=True))
    engaged_count = demand.engagements.count()
    my_proposal = None
    access = "none"
    if supplier:
        if _is_selected(demand, supplier.id):
            access = "selected"
        elif _is_engaged(demand, supplier.id):
            access = "engaged"
        proposal = next(
            (p for p in demand.proposals.all() if p.supplier_id == supplier.id),
            None,
        )
        if proposal:
            my_proposal = _serialize_proposal(proposal, include_supplier=False)

    payload = {
        "id": demand.id,
        "kind": demand.kind,
        "status": demand.status,
        "categoryId": demand.category_id,
        "categoryName": _category_name(demand.category),
        "subcategoryId": demand.subcategory_id,
        "subcategoryName": _category_name(demand.subcategory),
        "quantity": demand.quantity,
        "desiredLeadTimeDays": demand.desired_lead_time_days,
        "indicativeBudget": str(demand.indicative_budget)
        if demand.indicative_budget is not None
        else None,
        "attachmentUrl": _media_url(request, demand.attachment),
        "photos": [
            {"id": photo.id, "url": _media_url(request, photo.image)}
            for photo in demand.photos.all()
        ],
        "description": demand.description,
        "wilaya": demand.wilaya,
        "tradeScope": demand.trade_scope,
        "destinationCountry": demand.destination_country,
        "destinationCity": demand.destination_city,
        "createdAt": demand.created_at.isoformat(),
        "engagedCount": engaged_count,
        "engagedCap": PUBLIC_QUOTE_ENGAGED_CAP,
        "selectedSupplierIds": selected_ids,
        "selectedCount": len(selected_ids),
        "visibleOnMarket": demand.visible_on_market,
        "adminQueue": demand.admin_queue,
        "marketPublishedAt": demand.market_published_at.isoformat()
        if demand.market_published_at
        else None,
        "marketRemovedReason": demand.market_removed_reason or None,
        "canRelaunch": buyer_can_relaunch(demand),
        "marketDays": PUBLIC_MARKET_DAYS,
        "fromPaksuites": bool(demand.paksuites_component_id or demand.catalogue_line_id),
        "paksuitesOptionTitle": (
            demand.paksuites_component.option.title
            if demand.paksuites_component_id
            else demand.catalogue_line.ensemble.name
            if demand.catalogue_line_id
            else None
        ),
        "packagingProjectId": demand.packaging_project_id,
        "packagingItemId": demand.packaging_item_id,
        "packagingSpecVersion": demand.packaging_spec_version,
        "currentSpecVersion": (
            demand.packaging_item.spec_version if demand.packaging_item_id else None
        ),
    }

    if viewer == "buyer":
        payload["buyerId"] = demand.buyer_id
        payload["proposals"] = [
            _serialize_proposal(p)
            for p in demand.proposals.all()
            if p.status != QuoteProposal.Status.DRAFT
        ]
        payload["selectedSuppliers"] = [
            _serialize_supplier_brief(sel.supplier) for sel in demand.selections.all()
        ]
    else:
        payload["buyerCompany"] = (
            demand.buyer.company_name
            or demand.buyer.get_full_name()
            or demand.buyer.username
        )
        payload["access"] = access
        payload["myProposal"] = my_proposal
        workflow = _supplier_workflow_status(demand, supplier, my_proposal)
        payload["workflowStatus"] = workflow
        payload["workflowStatusLabel"] = QUOTE_WORKFLOW_LABELS.get(
            workflow, workflow
        )
        payload.update(_buyer_contact_payload(demand, request, supplier))
        payload["canRespond"] = _supplier_can_respond(demand, supplier)
        payload["canRespondOpportunity"] = _can_respond_opportunity(demand, supplier)
        payload["plan"] = get_supplier_plan_code(supplier)
        if demand.kind == QuoteDemand.Kind.PUBLIC:
            payload["banner"] = (
                f"Demande publique — {engaged_count}/{PUBLIC_QUOTE_ENGAGED_CAP} "
                "fournisseurs engagés"
            )
        elif demand.paksuites_component_id:
            payload["banner"] = (
                "PakSuites — cette demande fait partie d'une solution d'emballage Paknomik"
            )
        else:
            payload["banner"] = "Demande privée"

    return payload


def _demand_queryset():
    return QuoteDemand.objects.select_related(
        "buyer",
        "category",
        "subcategory",
        "packaging_item",
        "paksuites_component",
        "paksuites_component__option",
        "catalogue_line",
        "catalogue_line__ensemble",
    ).prefetch_related(
        Prefetch(
            "selections",
            queryset=QuoteDemandSelection.objects.select_related(
                "supplier", "supplier__score_record"
            ),
        ),
        Prefetch(
            "proposals",
            queryset=QuoteProposal.objects.select_related(
                "supplier",
                "supplier__score_record",
                "converted_order",
            ).prefetch_related("events"),
        ),
        "engagements",
        "photos",
    )


def _parse_int(value, field, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({"error": f"{field} est requis."})
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"error": f"{field} doit être un entier."}) from exc
    if parsed <= 0:
        raise ValidationError({"error": f"{field} doit être positif."})
    return parsed


def _parse_decimal(value, field):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"error": f"{field} est invalide."}) from exc
    if amount < 0:
        raise ValidationError({"error": f"{field} doit être positif."})
    return amount


def _proposal_valid_until_expired(proposal):
    return _terms_valid_until_passed(proposal.commercial_terms)


def _terms_valid_until_passed(terms):
    raw = (terms or {}).get("validUntil")
    if not raw:
        return False
    try:
        from datetime import datetime

        text = str(raw).replace("Z", "+00:00")
        if len(text) == 10:
            text = f"{text}T23:59:59+00:00"
        deadline = datetime.fromisoformat(text)
        if timezone.is_naive(deadline):
            deadline = timezone.make_aware(
                deadline, timezone.get_current_timezone()
            )
        return timezone.now() > deadline
    except (ValueError, TypeError):
        return False


def _empty_totals():
    return {
        "subtotal": "0.00",
        "discountTotal": "0.00",
        "extraFees": "0.00",
        "taxTotal": "0.00",
        "totalAmount": "0.00",
    }


def _sync_proposal_scalar_fields(proposal, lines, totals, data, demand):
    total = Decimal(totals["totalAmount"])
    qty = _parse_int(data.get("confirmedQuantity"), "confirmedQuantity", False)
    if not qty and lines:
        qty = sum(int(line["quantity"]) for line in lines)
    if not qty:
        qty = demand.quantity or 1
    proposal.confirmed_quantity = qty
    if total > 0 and qty > 0:
        proposal.unit_price = (total / Decimal(qty)).quantize(Decimal("0.01"))
    elif lines:
        proposal.unit_price = Decimal(str(lines[0]["unitPrice"]))
    else:
        unit_price = _parse_decimal(data.get("unitPrice"), "unitPrice")
        if unit_price is not None:
            proposal.unit_price = unit_price
    proposal.subtotal = Decimal(totals["subtotal"])
    proposal.discount_total = Decimal(totals["discountTotal"])
    proposal.extra_fees = Decimal(totals["extraFees"])
    proposal.tax_total = Decimal(totals["taxTotal"])
    proposal.total_amount = total


def _resolve_proposal_lines(data, existing, demand, *, sending: bool):
    raw_lines = data.get("lineItems")
    if raw_lines is None:
        raw_lines = data.get("lines")
    has_scalar = data.get("unitPrice") not in (None, "")
    if raw_lines is not None:
        return parse_line_items(
            raw_lines,
            require_at_least_one=sending,
        )
    if sending and has_scalar:
        unit_price = _parse_decimal(data.get("unitPrice"), "unitPrice")
        confirmed_qty = _parse_int(
            data.get("confirmedQuantity"), "confirmedQuantity", True
        )
        if unit_price is None:
            raise ValidationError(
                {"error": "Le prix unitaire est requis.", "code": "unit_price_required"}
            )
        description = (demand.description or demand.subcategory.name_fr or "")[:500]
        return legacy_line_from_scalar(
            unit_price=unit_price,
            quantity=confirmed_qty,
            description=description,
        )
    if existing and existing.line_items:
        return list(existing.line_items)
    if sending:
        raise ValidationError(
            {
                "error": "Ajoutez au moins une ligne commerciale.",
                "code": "lines_required",
            }
        )
    return []


def _product_for_proposal_order(proposal, demand):
    for line in proposal.line_items or []:
        product_id = line.get("productId")
        if not product_id:
            continue
        match = Product.objects.filter(
            pk=product_id,
            supplier=proposal.supplier,
            is_active=True,
        ).first()
        if match:
            return match
    return _catalog_product_for_quote(proposal, demand)


def _parse_id_list(raw):
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("["):
            import json

            try:
                values = json.loads(stripped)
            except json.JSONDecodeError:
                values = [part for part in stripped.split(",") if part.strip()]
        else:
            values = [part for part in stripped.split(",") if part.strip()]
    else:
        values = [raw]
    ids = []
    for item in values:
        try:
            ids.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"error": "Identifiant fournisseur invalide."}) from exc
    return list(dict.fromkeys(ids))


def _public_cooldown_payload(buyer):
    cutoff = timezone.now() - timedelta(hours=QUOTE_PUBLIC_COOLDOWN_HOURS)
    last = (
        QuoteDemand.objects.filter(
            buyer=buyer,
            kind=QuoteDemand.Kind.PUBLIC,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .first()
    )
    if not last:
        return None
    available_at = last.created_at + timedelta(hours=QUOTE_PUBLIC_COOLDOWN_HOURS)
    remaining = available_at - timezone.now()
    hours = max(1, int(remaining.total_seconds() // 3600))
    return {
        "error": (
            f"Une demande publique ne peut être créée que toutes les "
            f"{QUOTE_PUBLIC_COOLDOWN_HOURS} h. Prochaine publication dans "
            f"{hours} h."
        ),
        "code": "public_cooldown",
        "nextAvailableAt": available_at.isoformat(),
        "hoursRemaining": hours,
        "cooldownHours": QUOTE_PUBLIC_COOLDOWN_HOURS,
    }


def _notify_selected_suppliers(demand, suppliers, *, origin="", origin_label=""):
    buyer_name = (
        demand.buyer.company_name
        or demand.buyer.get_full_name()
        or demand.buyer.username
    )
    paksuites = origin == "paksuites"
    combination = f" « {origin_label} »" if paksuites and origin_label else ""
    subject = (
        "PakSuites — nouvelle demande de devis — "
        if paksuites
        else "Nouvelle demande de devis — "
    ) + _category_name(demand.subcategory)
    title = "PakSuites — vous êtes dans une solution" if paksuites else "Nouvelle demande de devis"
    body = (
        (
            f"{buyer_name} vous a inclus dans une solution PakSuites{combination} "
            f"({_category_name(demand.subcategory)}). "
            "PakSuites est une invention Paknomik : l'acheteur compose son emballage, "
            "et chaque composant devient une demande de devis."
        )
        if paksuites
        else (
            f"{buyer_name} vous a sélectionné pour une demande "
            f"{demand.get_kind_display().lower()} "
            f"({_category_name(demand.subcategory)})."
        )
    )
    for supplier in suppliers:
        # Entreprise + contact + opportunité + activité sont créés dès la
        # réception de la demande, sans action du fournisseur.
        crm.sync_from_quote_demand(demand, supplier)
        for user in supplier.users.all():
            _notify(
                user,
                AppNotification.Kind.QUOTE_INVITED,
                title,
                body,
                "/supplier/quotes",
                demand,
            )
        message = (
            f"Bonjour {supplier.company_name},\n\n"
            f"{body}\n\n"
            f"Catégorie : {_category_name(demand.category)}\n"
            f"Sous-catégorie : {_category_name(demand.subcategory)}\n"
            f"Quantité : {demand.quantity}\n"
        )
        if demand.description:
            message += f"\nDescription :\n{demand.description}\n"
        send_mail(subject, message, None, [supplier.email], fail_silently=True)


def _log_quote_created(demand, supplier_ids):
    log_buyer_event(
        demand.buyer,
        Event.EventType.BUYER_QUOTATION_REQUEST,
        {"quote_demand_id": demand.id, "kind": demand.kind},
    )
    for supplier_id in supplier_ids:
        apply_score_delta(
            supplier_id,
            "commercial",
            60,
            "commercial_rfq",
            extra_context={"quote_demand_id": demand.id},
        )
        log_system_event(
            Event.EventType.SELLER_QUOTATION_RECEIVED,
            {"supplier_id": supplier_id, "quote_demand_id": demand.id},
        )


class PragmadinarCatalogView(APIView):
    def get(self, request):
        if not PragmadinarService.objects.exists():
            seed_pragmadinar_catalog()
        plans = [
            {
                "code": plan.code,
                "name": plan.name,
                "monthlyPriceDzd": str(plan.monthly_price_dzd),
                "isDefault": plan.is_default,
            }
            for plan in PaknomikPlan.objects.all()
        ]
        services = []
        listing_order = {row["code"]: index for index, row in enumerate(CATALOG_SERVICES)}
        rows = sorted(
            PragmadinarService.objects.filter(is_active=True),
            key=lambda row: (
                LISTING_CLASS_ORDER.index(row.service_class)
                if row.service_class in LISTING_CLASS_ORDER
                else len(LISTING_CLASS_ORDER),
                listing_order.get(row.code, len(listing_order)),
                row.price_rank if row.price_rank is not None else 999,
                row.label,
            ),
        )
        for row in rows:
            cost = tariff_display_cost(row)
            services.append(
                {
                    "code": row.code,
                    "serviceClass": row.service_class,
                    "serviceClassLabel": LISTING_CLASS_LABELS.get(
                        row.service_class, row.service_class
                    ),
                    "priceRank": row.price_rank,
                    "onPriceList": on_price_list(row),
                    "unitLabel": row.unit_label,
                    "businessAccessLabel": access_label(row.business_access),
                    "genereuxAccessLabel": access_label(row.genereux_access, row.genereux_quota),
                    "walletCategory": row.wallet_category,
                    "label": row.label,
                    "costPragmadinar": f"{cost.quantize(Decimal('0.01'))}",
                    "unit": row.unit,
                    "unitQuantity": row.unit_quantity,
                    "costLabel": format_cost_label(
                        cost,
                        row.unit,
                        row.unit_quantity,
                        row.unit_label,
                    ),
                    "businessAccess": row.business_access,
                    "genereuxAccess": row.genereux_access,
                    "genereuxQuota": row.genereux_quota,
                    "isBilled": row.code not in UNBILLED_SERVICE_CODES,
                }
            )
        opportunity = next(
            (s for s in services if s["code"] == OPPORTUNITY_SERVICE_CODE),
            None,
        )
        return Response(
            {
                "plans": plans,
                "services": services,
                "opportunityServiceCode": OPPORTUNITY_SERVICE_CODE,
                "opportunityCostPragmadinar": opportunity["costPragmadinar"]
                if opportunity
                else "10",
                "publicCooldownHours": QUOTE_PUBLIC_COOLDOWN_HOURS,
                "engagedCap": PUBLIC_QUOTE_ENGAGED_CAP,
                "welcomePragmadinar": format_pragmadinar_amount(
                    supplier_welcome_pragmadinar()
                ),
                "welcomeSplitPercent": {
                    category: percent for category, percent in WELCOME_SPLIT_PERCENT
                },
            }
        )


class SupplierWalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier = _require_supplier(request)
        wallet = wallet_payload(supplier)
        available = Decimal(wallet["available"])
        entries = [
            {
                "id": entry.id,
                "amount": format_pragmadinar_amount(entry.amount),
                "reason": entry.reason,
                "serviceCode": entry.service.code if entry.service_id else None,
                "walletCategory": entry.wallet.category,
                "refundable": entry.refundable,
                "createdAt": entry.created_at.isoformat(),
            }
            for entry in PragmadinarLedger.objects.filter(wallet__supplier=supplier)
            .select_related("service", "wallet")[:40]
        ]
        pending = PragmadinarRechargeRequest.objects.filter(
            supplier=supplier,
            status=PragmadinarRechargeRequest.Status.PENDING,
        ).first()
        opportunity_cost = "0"
        try:
            opportunity_cost = str(opportunity_unlock_cost())
        except ValidationError:
            opportunity_cost = "10"
        return Response(
            {
                "balance": wallet["balance"],
                "available": format_pragmadinar_amount(available),
                "held": wallet["held"],
                "rateDzd": str(pragmadinar_rate_dzd()),
                "wallets": wallet["wallets"],
                "plan": get_supplier_plan_code(supplier),
                "planUntil": None,
                "opportunityCostPragmadinar": opportunity_cost,
                "opportunityServiceCode": OPPORTUNITY_SERVICE_CODE,
                "entries": entries,
                "pendingRecharge": {
                    "id": pending.id,
                    "amount": format_pragmadinar_amount(pending.amount),
                    "createdAt": pending.created_at.isoformat(),
                }
                if pending
                else None,
            }
        )


class SupplierWalletRechargeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        supplier = _require_supplier(request)
        amount = _parse_decimal(request.data.get("amount"), "amount")
        if amount is None or amount <= 0:
            return Response(
                {"error": "Indiquez un montant de recharge positif."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        note = (request.data.get("note") or "").strip()
        existing = PragmadinarRechargeRequest.objects.filter(
            supplier=supplier,
            status=PragmadinarRechargeRequest.Status.PENDING,
        ).first()
        if existing:
            return Response(
                {
                    "error": "Une demande de recharge est déjà en attente.",
                    "id": existing.id,
                },
                status=status.HTTP_409_CONFLICT,
            )
        recharge = PragmadinarRechargeRequest.objects.create(
            supplier=supplier,
            amount=amount,
            note=note,
        )
        return Response(
            {
                "success": True,
                "id": recharge.id,
                "message": (
                    "Demande de recharge envoyée. Un administrateur Paknomik "
                    "créditera votre solde après validation."
                ),
            },
            status=status.HTTP_201_CREATED,
        )


def _technical_characteristics(data):
    raw = data.get("technicalCharacteristics") if hasattr(data, "get") else None
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


class QuoteDemandCandidateSuppliersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        subcategory_id = request.query_params.get("subcategory")
        category_id = request.query_params.get("category")
        if not subcategory_id and not category_id:
            return Response(
                {"error": "category ou subcategory est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        filters = Q(is_active=True, is_ghost=False)
        if subcategory_id:
            filters &= (
                Q(interested_subcategories=subcategory_id)
                | Q(products__category_id=subcategory_id, products__is_active=True)
                | Q(products__subcategory_id=subcategory_id, products__is_active=True)
            )
        if category_id:
            filters &= (
                Q(interested_categories=category_id)
                | Q(products__category_id=category_id, products__is_active=True)
                | Q(interested_subcategories__parent_id=category_id)
            )
        suppliers = (
            Supplier.objects.filter(filters)
            .distinct()
            .select_related("score_record")
            .order_by("company_name")[:80]
        )
        return Response([_serialize_supplier_brief(s) for s in suppliers])


class QuoteDemandCooldownView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        payload = _public_cooldown_payload(request.user)
        return Response(
            {
                "canCreatePublic": payload is None,
                "cooldownHours": QUOTE_PUBLIC_COOLDOWN_HOURS,
                **(payload or {}),
            }
        )


def _link_market_quote_to_dossier(user, demand, data):
    """Rattache un devis marché au projet ou à la fiche, si l'acheteur les possède."""
    raw_project = data.get("packagingProjectId")
    raw_item = data.get("packagingItemId")
    if raw_project in (None, "") and raw_item in (None, ""):
        return None
    from buyer_space.models import BuyerAccount, PackagingItem, PackagingProject

    try:
        account = user.buyer_account
    except BuyerAccount.DoesNotExist:
        return "Compte acheteur requis."
    item = None
    project = None
    if raw_item not in (None, ""):
        item = PackagingItem.objects.filter(pk=raw_item, project__account=account).first()
        if item is None:
            return "Emballage introuvable."
    if raw_project not in (None, ""):
        project = PackagingProject.objects.filter(pk=raw_project, account=account).first()
        if project is None:
            return "Projet introuvable."
    if item is not None and project is not None and item.project_id != project.id:
        return "L'emballage n'appartient pas à ce projet."
    if item is not None and project is None:
        project = item.project
    demand.packaging_project = project
    demand.packaging_item = item
    demand.packaging_spec_version = item.spec_version if item is not None else None
    demand.save(update_fields=["packaging_project", "packaging_item", "packaging_spec_version"])
    return None


class BuyerQuoteDemandListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        expire_stale_public_market_listings()
        demands = _demand_queryset().filter(buyer=request.user)
        return Response(
            [_serialize_demand(d, request, viewer="buyer") for d in demands]
        )

    def post(self, request):
        kind = (request.data.get("kind") or QuoteDemand.Kind.PUBLIC).strip()
        if kind not in (QuoteDemand.Kind.PUBLIC, QuoteDemand.Kind.PRIVATE):
            return Response(
                {"error": "kind doit être public ou private."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if kind == QuoteDemand.Kind.PUBLIC:
            cooldown = _public_cooldown_payload(request.user)
            if cooldown:
                return Response(cooldown, status=status.HTTP_429_TOO_MANY_REQUESTS)

        try:
            category_id = _parse_int(request.data.get("categoryId"), "categoryId", True)
            subcategory_id = _parse_int(
                request.data.get("subcategoryId"), "subcategoryId", True
            )
            quantity = _parse_int(request.data.get("quantity"), "quantity", True)
            desired = _parse_int(
                request.data.get("desiredLeadTimeDays"), "desiredLeadTimeDays"
            )
            budget = _parse_decimal(
                request.data.get("indicativeBudget"), "indicativeBudget"
            )
            selected_ids = _parse_id_list(
                request.data.get("selectedSupplierIds")
                or request.data.get("selectedSupplierIds[]")
            )
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        description = (request.data.get("description") or "").strip()
        if not description:
            return Response(
                {"error": "La description est requise."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            category = Category.objects.get(pk=category_id, parent__isnull=True)
            subcategory = Category.objects.select_related("parent").get(
                pk=subcategory_id, parent__isnull=False
            )
        except Category.DoesNotExist:
            return Response(
                {"error": "Catégorie ou sous-catégorie invalide."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if subcategory.parent_id != category.id:
            return Response(
                {"error": "La sous-catégorie n'appartient pas à cette catégorie."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if kind == QuoteDemand.Kind.PRIVATE and not selected_ids:
            return Response(
                {"error": "Une demande privée doit cibler au moins un fournisseur."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        attachment = request.FILES.get("attachment")
        if attachment:
            if attachment.size > MAX_ATTACHMENT_BYTES:
                return Response(
                    {"error": "La pièce jointe dépasse 10 Mo."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            content_type = getattr(attachment, "content_type", "") or ""
            if content_type and content_type not in ALLOWED_ATTACHMENT_TYPES:
                return Response(
                    {"error": "Format de pièce jointe non pris en charge."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        photos = request.FILES.getlist("photos")
        photo_error = clean_photos(photos)
        if photo_error:
            return Response({"error": photo_error}, status=status.HTTP_400_BAD_REQUEST)
        photos = sanitized_photos(photos)
        if attachment and (getattr(attachment, "content_type", "") or "").startswith("image/"):
            attachment = strip_metadata(attachment) or attachment

        suppliers = list(
            Supplier.objects.filter(pk__in=selected_ids, is_active=True, is_ghost=False).prefetch_related(
                "users"
            )
        )
        if selected_ids and len(suppliers) != len(selected_ids):
            return Response(
                {"error": "Un fournisseur sélectionné est introuvable."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            trade_scope = clean_trade_scope(request.data.get("tradeScope"))
            destination_country = (
                clean_export_country(request.data.get("destinationCountry"), required=True)
                if trade_scope == TRADE_EXPORT
                else ""
            )
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        destination_city = (
            str(request.data.get("destinationCity") or "").strip()[:160]
            if trade_scope == TRADE_EXPORT
            else ""
        )

        market_fields = initial_market_fields(
            kind=kind, has_selections=bool(selected_ids)
        )
        demand = QuoteDemand.objects.create(
            buyer=request.user,
            kind=kind,
            category=category,
            subcategory=subcategory,
            quantity=quantity,
            desired_lead_time_days=desired,
            indicative_budget=budget,
            attachment=attachment,
            description=description,
            wilaya=(request.user.wilaya or "").strip(),
            trade_scope=trade_scope,
            destination_country=destination_country,
            destination_city=destination_city,
            **market_fields,
        )
        characteristics = _technical_characteristics(request.data)
        type_id = request.data.get("productTypeId") or request.data.get("catalogTypeId")
        updates = []
        if characteristics:
            demand.technical_characteristics = characteristics
            updates.append("technical_characteristics")
        if type_id and str(type_id).isdigit():
            from user.models import ProductType

            catalog_type = ProductType.objects.filter(pk=int(type_id)).first()
            if catalog_type is not None:
                demand.catalog_type = catalog_type
                updates.append("catalog_type")
        if updates:
            demand.save(update_fields=updates)
        save_photos(QuoteDemandPhoto, "demand", demand, photos)
        for supplier in suppliers:
            QuoteDemandSelection.objects.create(demand=demand, supplier=supplier)

        link_error = _link_market_quote_to_dossier(request.user, demand, request.data)
        if link_error:
            demand.delete()
            return Response({"error": link_error}, status=status.HTTP_400_BAD_REQUEST)

        _notify_selected_suppliers(demand, suppliers)
        _log_quote_created(demand, [s.id for s in suppliers])

        demand = _demand_queryset().get(pk=demand.pk)
        return Response(
            {
                "success": True,
                "id": demand.id,
                "message": (
                    "Votre demande de devis privée a été envoyée."
                    if kind == QuoteDemand.Kind.PRIVATE
                    else (
                        "Votre demande est enregistrée et transmise aux fournisseurs "
                        "sélectionnés (hors marché public)."
                        if selected_ids
                        else "Votre demande de devis publique est visible sur le marché "
                        f"pour {PUBLIC_MARKET_DAYS} jours."
                    )
                ),
                "notifiedSupplierCount": len(suppliers),
                "demand": _serialize_demand(demand, request, viewer="buyer"),
            },
            status=status.HTTP_201_CREATED,
        )


class BuyerQuoteDemandRelaunchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        demand = _demand_queryset().filter(pk=pk, buyer=request.user).first()
        if not demand:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not buyer_can_relaunch(demand):
            return Response(
                {
                    "error": (
                        "Cette demande ne peut pas être remise sur le marché "
                        "(déjà visible, résolue ou privée)."
                    ),
                    "code": "relaunch_not_allowed",
                },
                status=status.HTTP_409_CONFLICT,
            )
        now = timezone.now()
        demand.visible_on_market = True
        demand.market_published_at = now
        demand.admin_queue = False
        demand.market_removed_reason = ""
        if demand.status == QuoteDemand.Status.CLOSED:
            demand.status = QuoteDemand.Status.OPEN
        demand.save(
            update_fields=[
                "visible_on_market",
                "market_published_at",
                "admin_queue",
                "market_removed_reason",
                "status",
                "updated_at",
            ]
        )
        demand = _demand_queryset().get(pk=demand.pk)
        return Response(
            {
                "success": True,
                "message": (
                    f"Demande republiée sur le marché pour {PUBLIC_MARKET_DAYS} jours."
                ),
                "demand": _serialize_demand(demand, request, viewer="buyer"),
            }
        )


class BuyerQuoteDemandDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        demand = _demand_queryset().filter(pk=pk, buyer=request.user).first()
        if not demand:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_serialize_demand(demand, request, viewer="buyer"))


def _catalog_product_for_quote(proposal, demand):
    """Pick a catalogue line for a quote-born order (subcategory → category → any)."""
    base = Product.objects.filter(supplier=proposal.supplier, is_active=True)
    if demand.subcategory_id:
        match = base.filter(subcategory_id=demand.subcategory_id).order_by("-updated_at").first()
        if match:
            return match
    match = base.filter(category_id=demand.category_id).order_by("-updated_at").first()
    if match:
        return match
    return base.order_by("-updated_at").first()


ORDER_DETAIL_FIELDS = {
    "buyerPoReference": ("buyer_po_reference", 120),
    "billingAddress": ("billing_address", 2000),
    "deliveryAddress": ("delivery_address", 2000),
    "deliveryInstructions": ("delivery_instructions", 2000),
    "receiverContact": ("receiver_contact", 255),
}


def _order_details_from_request(data):
    """Optional buyer-side order form fields sent with an acceptance."""
    raw = data.get("order") if isinstance(data.get("order"), dict) else data
    details = {}
    for key, (field, max_len) in ORDER_DETAIL_FIELDS.items():
        value = raw.get(key)
        if value not in (None, ""):
            details[field] = str(value).strip()[:max_len]
    expected = raw.get("expectedDeliveryDate")
    if expected not in (None, ""):
        try:
            parsed = date.fromisoformat(str(expected)[:10])
        except ValueError as exc:
            raise ValidationError(
                {"error": "Date de livraison souhaitée invalide.", "code": "invalid_date"}
            ) from exc
        if parsed < timezone.localdate():
            raise ValidationError(
                {
                    "error": "La date de livraison souhaitée est déjà passée.",
                    "code": "invalid_date",
                }
            )
        details["expected_delivery_date"] = parsed
    return details


def _create_order_from_proposal(buyer, demand, proposal, details=None):
    """Record the order born from an accepted proposal (idempotent, never billed).

    Returns ``(order, created)``; raises ValidationError when the supplier has no
    active catalogue product to attach the order to.
    """
    from user.api.views import _create_buyer_message, handle_order_created

    existing = Order.objects.filter(quote_proposal=proposal).first()
    if existing:
        return existing, False

    product = _product_for_proposal_order(proposal, demand)
    if not product:
        raise ValidationError(
            {
                "error": (
                    "Ce fournisseur n'a pas de produit catalogue actif. "
                    "Contactez-le via la messagerie pour finaliser la commande."
                ),
                "code": "no_catalog_product",
            }
        )

    # The accepted version is frozen: catalogue values never replace it.
    snapshot = proposal.content_snapshot or proposal_snapshot(proposal)
    spec_parts = [f"Commande issue du devis #{demand.id} (proposition v{proposal.version})."]
    if (demand.description or "").strip():
        spec_parts.append(demand.description.strip())
    if (proposal.message or "").strip():
        spec_parts.append(proposal.message.strip())

    terms = snapshot.get("commercialTerms") or {}
    is_export = (
        terms.get("tradeScope") == TRADE_EXPORT or demand.trade_scope == TRADE_EXPORT
    )
    trade_fields = {"trade_scope": TradeScope.EXPORT if is_export else TradeScope.LOCAL}
    if is_export:
        trade_fields.update(
            {
                "destination_country": (
                    terms.get("destinationCountry") or demand.destination_country or ""
                )[:2],
                "destination_city": (demand.destination_city or "")[:160],
                "incoterm": (terms.get("incoterm") or "")[:8],
            }
        )
    else:
        trade_fields["delivery_wilaya"] = (demand.wilaya or "")[:100]

    with transaction.atomic():
        order = Order.objects.create(
            buyer=buyer,
            supplier=proposal.supplier,
            product=product,
            quantity=proposal.confirmed_quantity,
            unit_price=proposal.unit_price,
            specifications="\n\n".join(spec_parts),
            commercial_snapshot=snapshot,
            quote_proposal=proposal,
            status=Order.Status.CONFIRMED,
            origin=Order.Origin.QUOTE_DEMAND,
            **trade_fields,
            **(details or {}),
        )
        register_order_commission(order)
        record_status_event(
            order,
            from_status="",
            to_status=Order.Status.CONFIRMED,
            actor=buyer,
            actor_role="buyer",
            note=f"Commande enregistrée depuis la proposition #{proposal.id}.",
        )
        _log_proposal_event(
            proposal,
            QuoteProposalEvent.Action.CONVERTED,
            buyer,
            comment=f"Commande #{order.id}",
        )
        quote_opp = Opportunity.objects.filter(
            demand=demand, supplier=proposal.supplier
        ).first()
        if quote_opp:
            quote_opp.order = order
            quote_opp.save(update_fields=["order", "updated_at"])
        else:
            try:
                crm.sync_from_order(order)
            except Exception:
                logger.exception("CRM sync failed for quote-born order %s", order.pk)

    log_buyer_event(
        buyer,
        Event.EventType.BUYER_ORDER_CREATED,
        {
            "order_id": order.id,
            "quote_demand_id": demand.id,
            "proposal_id": proposal.id,
        },
    )
    total = snapshot.get("totals", {}).get("totalAmount") if snapshot else None
    order_note = (
        f"Commande #{order.id} enregistrée depuis le devis #{demand.id} — "
        f"{proposal.confirmed_quantity} u."
        + (f" · total {total} {proposal.currency}" if total else "")
        + "."
    )
    _create_buyer_message(proposal.supplier, buyer, order_note)
    handle_order_created(proposal.supplier_id, order.id, buyer=buyer)
    return order, True


class BuyerQuoteDemandDecideView(APIView):
    """Buyer decision on a proposal: accept (→ order), refuse or request a revision.

    None of these actions consumes Pragmadinar, for the buyer or the supplier.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        demand = _demand_queryset().filter(pk=pk, buyer=request.user).first()
        if not demand:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if demand.status == QuoteDemand.Status.RESOLVED:
            return Response(
                {"error": "Cette demande est déjà résolue."},
                status=status.HTTP_409_CONFLICT,
            )

        decision = (request.data.get("decision") or "accept").strip()
        if decision not in ("accept", "refuse", "request_revision"):
            return Response(
                {"error": "Décision inconnue.", "code": "invalid_decision"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        proposal_id = request.data.get("proposalId")
        proposal = demand.proposals.filter(pk=proposal_id).first() if proposal_id else None
        if not proposal or proposal.status == QuoteProposal.Status.DRAFT:
            return Response(
                {"error": "Proposition introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        comment = (request.data.get("comment") or "").strip()[:2000]

        if decision == "request_revision":
            return self._request_revision(request, demand, proposal, comment)
        if decision == "refuse":
            return self._refuse(request, demand, proposal, comment)
        return self._accept(request, demand, proposal, comment)

    def _demand_response(self, request, demand, **extra):
        payload = _serialize_demand(
            _demand_queryset().get(pk=demand.pk), request, viewer="buyer"
        )
        payload.update(extra)
        return Response(payload)

    def _request_revision(self, request, demand, proposal, comment):
        if proposal.status != QuoteProposal.Status.SENT:
            return Response(
                {
                    "error": (
                        "Seule une proposition envoyée peut faire l'objet "
                        "d'une demande de révision."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not comment:
            return Response(
                {
                    "error": "Précisez les modifications attendues.",
                    "code": "revision_comment_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        proposal.status = QuoteProposal.Status.REVISION_REQUESTED
        proposal.buyer_response_comment = comment
        proposal.responded_by = request.user
        proposal.save(
            update_fields=[
                "status",
                "buyer_response_comment",
                "responded_by",
                "updated_at",
            ]
        )
        _log_proposal_event(
            proposal,
            QuoteProposalEvent.Action.REVISION_REQUESTED,
            request.user,
            comment=comment,
        )
        for user in proposal.supplier.users.all():
            _notify(
                user,
                AppNotification.Kind.QUOTE_PROPOSAL,
                "Révision demandée",
                (
                    f"L'acheteur demande une révision de votre proposition "
                    f"pour le devis #{demand.id} : {comment[:200]}"
                ),
                f"/supplier/quotes?reply={demand.id}",
                demand,
            )
        Message.objects.create(
            supplier=proposal.supplier,
            buyer=request.user,
            sender=request.user,
            content=f"Révision demandée (devis #{demand.id}) :\n{comment}",
        )
        return self._demand_response(request, demand)

    def _refuse(self, request, demand, proposal, comment):
        if proposal.status not in (
            QuoteProposal.Status.SENT,
            QuoteProposal.Status.REVISION_REQUESTED,
        ):
            return Response(
                {"error": "Cette proposition ne peut plus être refusée."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        now = timezone.now()
        proposal.status = QuoteProposal.Status.LOST
        proposal.refused_at = now
        proposal.responded_by = request.user
        if comment:
            proposal.buyer_response_comment = comment
        proposal.save(
            update_fields=[
                "status",
                "refused_at",
                "responded_by",
                "buyer_response_comment",
                "updated_at",
            ]
        )
        _log_proposal_event(
            proposal, QuoteProposalEvent.Action.REFUSED, request.user, comment=comment
        )

        if demand.kind == QuoteDemand.Kind.PRIVATE:
            demand.status = QuoteDemand.Status.CLOSED
            demand.save(update_fields=["status", "updated_at"])
            _close_opportunities(demand, winner_id=None)
            _notify_losers(demand, winner_id=None)
            return self._demand_response(request, demand)

        # Public demand: only this supplier is turned down, the others stay in play.
        opportunity = Opportunity.objects.filter(
            demand=demand, supplier=proposal.supplier
        ).first()
        if opportunity:
            crm.set_stage(
                opportunity,
                Opportunity.Stage.LOST,
                lost_reason="Proposition refusée par l'acheteur.",
            )
        for user in proposal.supplier.users.all():
            _notify(
                user,
                AppNotification.Kind.QUOTE_LOST,
                "Proposition non retenue",
                f"Votre proposition pour la demande de devis #{demand.id} a été refusée.",
                "/supplier/quotes",
                demand,
            )
        return self._demand_response(request, demand)

    def _accept(self, request, demand, proposal, comment):
        if proposal.status != QuoteProposal.Status.SENT:
            return Response(
                {"error": "Seule une proposition envoyée peut être acceptée."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if _proposal_valid_until_expired(proposal):
            proposal.status = QuoteProposal.Status.EXPIRED
            proposal.save(update_fields=["status", "updated_at"])
            _log_proposal_event(proposal, QuoteProposalEvent.Action.EXPIRED, request.user)
            return Response(
                {"error": "Cette proposition a expiré.", "code": "proposal_expired"},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            details = _order_details_from_request(request.data)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            demand.proposals.exclude(pk=proposal.pk).exclude(
                status=QuoteProposal.Status.DRAFT
            ).update(status=QuoteProposal.Status.LOST)
            proposal.status = QuoteProposal.Status.WON
            proposal.accepted_at = timezone.now()
            proposal.content_snapshot = proposal_snapshot(proposal)
            proposal.responded_by = request.user
            if comment:
                proposal.buyer_response_comment = comment
            proposal.save(
                update_fields=[
                    "status",
                    "content_snapshot",
                    "accepted_at",
                    "responded_by",
                    "buyer_response_comment",
                    "updated_at",
                ]
            )
            _log_proposal_event(
                proposal, QuoteProposalEvent.Action.ACCEPTED, request.user, comment=comment
            )
            demand.status = QuoteDemand.Status.RESOLVED
            demand.save(update_fields=["status", "updated_at"])
            _close_opportunities(demand, winner_id=proposal.supplier_id)

        _notify_losers(demand, winner_id=proposal.supplier_id)
        log_buyer_event(
            request.user,
            Event.EventType.BUYER_QUOTATION_ACCEPTED,
            {"quote_demand_id": demand.id, "proposal_id": proposal.id},
        )

        extra = {}
        try:
            order, _created = _create_order_from_proposal(
                request.user, demand, proposal, details
            )
            extra["orderId"] = order.id
        except ValidationError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            extra["orderError"] = str(detail.get("error") or "Commande non créée.")
        order_id = extra.get("orderId")
        accepted_body = (
            f"Votre proposition pour la demande de devis #{demand.id} a été acceptée"
            + (f" — commande #{order_id} enregistrée." if order_id else ".")
        )
        for user in proposal.supplier.users.all():
            _notify(
                user,
                AppNotification.Kind.QUOTE_PROPOSAL,
                "Proposition acceptée",
                accepted_body,
                "/supplier/orders" if order_id else "/supplier/quotes",
                demand,
            )
        return self._demand_response(request, demand, **extra)


class BuyerQuoteDemandConvertToOrderView(APIView):
    """Fallback for proposals accepted before orders were created automatically."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        demand = _demand_queryset().filter(pk=pk, buyer=request.user).first()
        if not demand:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if demand.status != QuoteDemand.Status.RESOLVED:
            return Response(
                {"error": "Acceptez d'abord une proposition avant de créer une commande."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        proposal_id = request.data.get("proposalId")
        proposal = (
            demand.proposals.filter(pk=proposal_id).first() if proposal_id else None
        )
        if not proposal or proposal.status != QuoteProposal.Status.WON:
            return Response(
                {"error": "Proposition gagnée introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            details = _order_details_from_request(request.data)
            order, created = _create_order_from_proposal(
                request.user, demand, proposal, details
            )
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        order = (
            Order.objects.filter(pk=order.pk)
            .select_related("product", "supplier", "quote_proposal")
            .prefetch_related("status_events")
            .first()
        )
        return Response(
            {
                "order": BuyerOrderSerializer(order, context={"request": request}).data,
                "alreadyExists": not created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _close_opportunities(demand, winner_id):
    """Mirror the buyer's decision in every supplier's pipeline.

    Losing a deal never triggers a Pragmadinar refund: what was paid was the access to
    the request, not the win.
    """
    for opportunity in Opportunity.objects.filter(demand=demand):
        if winner_id and opportunity.supplier_id == winner_id:
            crm.set_stage(
                opportunity,
                Opportunity.Stage.WON,
                note="Proposition retenue par l'acheteur.",
            )
        else:
            crm.set_stage(
                opportunity,
                Opportunity.Stage.LOST,
                lost_reason=(
                    "Proposition non retenue par l'acheteur."
                    if winner_id
                    else "Demande refusée par l'acheteur."
                ),
            )


def _notify_losers(demand, winner_id):
    engaged_ids = set(demand.engagements.values_list("supplier_id", flat=True))
    selected_replied = set(
        demand.proposals.values_list("supplier_id", flat=True)
    )
    loser_ids = (engaged_ids | selected_replied) - ({winner_id} if winner_id else set())
    suppliers = Supplier.objects.filter(pk__in=loser_ids).prefetch_related("users")
    for supplier in suppliers:
        for user in supplier.users.all():
            _notify(
                user,
                AppNotification.Kind.QUOTE_LOST,
                "Proposition non retenue",
                (
                    f"La demande de devis #{demand.id} a été attribuée "
                    "à un autre fournisseur."
                    if winner_id
                    else f"La demande de devis #{demand.id} a été clôturée."
                ),
                "/supplier/quotes",
                demand,
            )


class SupplierQuoteDemandListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier = _require_supplier(request)
        demands = (
            _demand_queryset()
            .filter(
                Q(selections__supplier=supplier) | Q(engagements__supplier=supplier)
            )
            .distinct()
        )
        serialized = [
            _serialize_demand(d, request, viewer="supplier", supplier=supplier)
            for d in demands
        ]
        return Response(
            {
                "results": serialized,
                "paksuites": _supplier_paksuites_performance(supplier),
            }
        )


def _supplier_paksuites_performance(supplier):
    """X demandes reçues, dont Y issues d'une solution PakSuites."""
    received = QuoteDemand.objects.filter(
        Q(selections__supplier=supplier) | Q(engagements__supplier=supplier)
    ).distinct()
    return {
        "quoteRequestCount": received.count(),
        "paksuitesCount": received.filter(
            Q(paksuites_component__isnull=False) | Q(catalogue_line__isnull=False)
        ).count(),
    }


class SupplierOpportunityListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier = _require_supplier(request)
        expire_stale_public_market_listings()
        category_ids, subcategory_ids = _supplier_category_ids(supplier)

        qs = _demand_queryset().filter(
            kind=QuoteDemand.Kind.PUBLIC,
            status=QuoteDemand.Status.OPEN,
            visible_on_market=True,
            admin_queue=False,
        )
        if subcategory_ids or category_ids:
            qs = qs.filter(
                Q(subcategory_id__in=subcategory_ids) | Q(category_id__in=category_ids)
            )
        else:
            qs = qs.none()

        qs = qs.exclude(selections__supplier=supplier).exclude(
            engagements__supplier=supplier
        ).distinct()

        wilaya_filter = request.query_params.get("wilaya")
        if wilaya_filter == "1" and supplier.wilaya:
            qs = qs.filter(Q(wilaya="") | Q(wilaya=supplier.wilaya))

        plan_code = get_supplier_plan_code(supplier)
        return Response(
            {
                "opportunityCostPragmadinar": "0",
                "opportunityServiceCode": OPPORTUNITY_SERVICE_CODE,
                "unlockEnabled": True,
                "participationFree": True,
                "plan": plan_code,
                "businessUnlockFree": True,
                "results": [
                    _serialize_demand(d, request, viewer="supplier", supplier=supplier)
                    for d in qs
                ],
            }
        )


class SupplierOpportunityUnlockView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        supplier = _require_supplier(request)
        demand = _demand_queryset().filter(pk=pk).first()
        if not demand or demand.kind != QuoteDemand.Kind.PUBLIC:
            return Response(
                {"error": "Opportunité introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if demand.status != QuoteDemand.Status.OPEN:
            return Response(
                {"error": "Cette demande n'est plus ouverte."},
                status=status.HTTP_409_CONFLICT,
            )
        if _is_selected(demand, supplier.id) or _is_engaged(demand, supplier.id):
            return Response(
                _serialize_demand(demand, request, viewer="supplier", supplier=supplier)
            )
        if demand.engagements.count() >= PUBLIC_QUOTE_ENGAGED_CAP:
            return Response(
                {"error": "Le plafond de 4 fournisseurs engagés est atteint."},
                status=status.HTTP_409_CONFLICT,
            )
        if not demand.visible_on_market or demand.admin_queue:
            return Response(
                {"error": "Cette demande n'est plus sur le marché public."},
                status=status.HTTP_409_CONFLICT,
            )
        if not _supplier_matches_demand_category(demand, supplier):
            return Response(
                {
                    "error": "Cette demande ne correspond pas à vos catégories.",
                    "code": "category_mismatch",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Participation in a public demand is free for every eligible supplier.
        _mark_engaged(demand, supplier, QuoteDemandEngagement.Source.PUBLIC_JOINED)
        demand = _demand_queryset().get(pk=demand.pk)
        return Response(
            _serialize_demand(demand, request, viewer="supplier", supplier=supplier)
        )


class SupplierQuoteDemandRespondView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        supplier = _require_supplier(request)
        demand = _demand_queryset().filter(pk=pk).first()
        if not demand:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if demand.status == QuoteDemand.Status.RESOLVED:
            return Response(
                {"error": "Cette demande est déjà résolue."},
                status=status.HTTP_409_CONFLICT,
            )
        existing = demand.proposals.filter(supplier=supplier).first()
        if existing and not _proposal_editable(existing):
            return Response(
                {
                    "error": (
                        "Vous avez déjà envoyé une proposition. Elle ne peut être "
                        "modifiée que si l'acheteur demande une révision."
                    ),
                    "code": "proposal_locked",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if not _supplier_can_respond(demand, supplier):
            return Response(
                {
                    "error": "Vous ne pouvez pas répondre à cette demande.",
                    "code": "cannot_respond",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data
        save_as_draft = str(data.get("saveAsDraft") or "").lower() in ("1", "true", "yes")
        sending = not save_as_draft
        try:
            lines = _resolve_proposal_lines(data, existing, demand, sending=sending)
            terms = parse_commercial_terms(
                data.get("commercialTerms"),
                require_complete=sending,
                default_trade_scope=demand.trade_scope,
            )
            currency = _parse_currency(data.get("currency"))
            lead_time = _parse_int(data.get("leadTimeDays"), "leadTimeDays", False)
            if not lead_time and terms.get("productionLeadDays"):
                lead_time = _parse_int(
                    terms.get("productionLeadDays"), "productionLeadDays", False
                )
            moq = _parse_int(data.get("moq"), "moq", False)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        lead_time = lead_time or (existing.lead_time_days if existing else None)
        if sending and not lead_time:
            return Response(
                {
                    "error": "Indiquez le délai de réalisation (jours).",
                    "code": "lead_time_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sending and _terms_valid_until_passed(terms):
            return Response(
                {
                    "error": "La date de validité de l'offre est déjà dépassée.",
                    "code": "valid_until_past",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        totals = compute_totals(lines, terms) if lines else _empty_totals()
        message = (data.get("message") or "").strip()

        proposal = existing or QuoteProposal(
            demand=demand, supplier=supplier, unit_price=Decimal("0")
        )
        previous_status = existing.status if existing else None
        proposal.line_items = lines
        proposal.commercial_terms = terms
        proposal.currency = currency
        proposal.message = message
        proposal.lead_time_days = lead_time or 1
        proposal.moq = moq or (existing.moq if existing else 1)
        _sync_proposal_scalar_fields(proposal, lines, totals, data, demand)

        if save_as_draft:
            if previous_status != QuoteProposal.Status.REVISION_REQUESTED:
                proposal.status = QuoteProposal.Status.DRAFT
            proposal.save()
            _log_proposal_event(
                proposal, QuoteProposalEvent.Action.DRAFT_SAVED, request.user
            )
            demand = _demand_queryset().get(pk=demand.pk)
            return Response(
                {
                    "success": True,
                    "draft": True,
                    "proposal": _serialize_proposal(proposal, include_supplier=False),
                    "demand": _serialize_demand(
                        demand, request, viewer="supplier", supplier=supplier
                    ),
                },
                status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
            )

        if (
            not _is_selected(demand, supplier.id)
            and not _is_engaged(demand, supplier.id)
            and demand.engagements.count() >= PUBLIC_QUOTE_ENGAGED_CAP
        ):
            return Response(
                {
                    "error": "Le plafond de 4 fournisseurs engagés est atteint.",
                    "code": "engaged_cap_reached",
                },
                status=status.HTTP_409_CONFLICT,
            )

        is_revision = previous_status == QuoteProposal.Status.REVISION_REQUESTED
        with transaction.atomic():
            if is_revision:
                proposal.version = (proposal.version or 1) + 1
                proposal.buyer_response_comment = ""
            proposal.status = QuoteProposal.Status.SENT
            proposal.sent_at = timezone.now()
            proposal.save()
            action = (
                QuoteProposalEvent.Action.REVISED
                if is_revision
                else QuoteProposalEvent.Action.SENT
            )
            _log_proposal_event(proposal, action, request.user, comment=message)

            if _is_selected(demand, supplier.id):
                _mark_engaged(
                    demand,
                    supplier,
                    QuoteDemandEngagement.Source.SELECTED_REPLIED,
                )
            elif not _is_engaged(demand, supplier.id):
                _mark_engaged(
                    demand,
                    supplier,
                    QuoteDemandEngagement.Source.PUBLIC_JOINED,
                )

        total_amount = proposal.total_amount or (
            proposal.unit_price * proposal.confirmed_quantity
        )
        opportunity = crm.sync_from_quote_demand(demand, supplier)
        crm.set_stage(
            opportunity,
            Opportunity.Stage.QUOTE_SENT,
            actor=request.user,
            note=(
                f"Proposition #{proposal.id} v{proposal.version} "
                f"{'révisée' if is_revision else 'envoyée'}."
            ),
        )
        opportunity.estimated_value = total_amount
        opportunity.save(update_fields=["estimated_value", "updated_at"])
        if not is_revision:
            crm.schedule_followups(opportunity, created_by=request.user)

        _notify(
            demand.buyer,
            AppNotification.Kind.QUOTE_PROPOSAL,
            "Proposition révisée" if is_revision else "Nouvelle proposition reçue",
            (
                f"{supplier.company_name} a envoyé la version {proposal.version} de sa "
                f"proposition pour votre demande de devis #{demand.id}."
                if is_revision
                else f"{supplier.company_name} a répondu à votre demande de devis #{demand.id}."
            ),
            f"/buyer?tab=orders&view=devis&quote={demand.id}",
            demand,
        )
        if demand.buyer_id:
            label = (
                f"Proposition révisée (v{proposal.version})"
                if is_revision
                else "Proposition envoyée"
            )
            Message.objects.create(
                supplier=supplier,
                buyer=demand.buyer,
                sender=request.user,
                content=(
                    f"{label} : total {total_amount} {proposal.currency}, "
                    f"qté {proposal.confirmed_quantity}, délai {proposal.lead_time_days} j, "
                    f"MOQ {proposal.moq}."
                    + (f"\n{message}" if message else "")
                ),
            )

        demand = _demand_queryset().get(pk=demand.pk)
        return Response(
            {
                "success": True,
                "draft": False,
                "proposal": _serialize_proposal(proposal, include_supplier=False),
                "demand": _serialize_demand(
                    demand, request, viewer="supplier", supplier=supplier
                ),
            },
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )


class QuoteNotificationsSeenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        audience = request.data.get("audience")
        kinds = [
            AppNotification.Kind.QUOTE_INVITED,
            AppNotification.Kind.QUOTE_PROPOSAL,
            AppNotification.Kind.QUOTE_LOST,
            AppNotification.Kind.RFQ_IN_REVIEW,
        ]
        if audience == "supplier":
            kinds = [
                AppNotification.Kind.QUOTE_INVITED,
                AppNotification.Kind.QUOTE_LOST,
            ]
        elif audience == "buyer":
            kinds = [
                AppNotification.Kind.QUOTE_PROPOSAL,
                AppNotification.Kind.RFQ_IN_REVIEW,
            ]
        AppNotification.objects.filter(
            user=request.user,
            is_read=False,
            kind__in=kinds,
        ).update(is_read=True)
        return Response({"ok": True})


def create_private_demand_from_rfq(
    buyer,
    supplier,
    product,
    message,
    quantity_estimate=None,
    destination="",
    origin="",
    origin_label="",
):
    """Best-effort dual-write when a boutique RFQ has enough catalog data."""
    if not buyer:
        return None
    category = None
    subcategory = None
    if product and product.category_id and product.subcategory_id:
        category = product.category
        subcategory = product.subcategory
    else:
        subcategory = supplier.interested_subcategories.select_related("parent").first()
        if subcategory:
            category = subcategory.parent
        else:
            category = supplier.interested_categories.first()
            if category:
                subcategory = category.children.first() if hasattr(category, "children") else None
    if not category or not subcategory:
        return None
    demand = QuoteDemand.objects.create(
        buyer=buyer,
        kind=QuoteDemand.Kind.PRIVATE,
        category=category,
        subcategory=subcategory,
        quantity=quantity_estimate or 1,
        description="\n".join(part for part in [message.strip(), f"Destination : {destination}" if destination else ""] if part),
        wilaya=(destination or buyer.wilaya or "").strip(),
        **initial_market_fields(kind=QuoteDemand.Kind.PRIVATE, has_selections=True),
    )
    QuoteDemandSelection.objects.create(demand=demand, supplier=supplier)
    _notify_selected_suppliers(
        demand, [supplier], origin=origin, origin_label=origin_label
    )
    return demand


class BuyerQuoteDemandRevalidateView(APIView):
    """Records the current packaging sheet version on an existing quote."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        demand = (
            _demand_queryset()
            .filter(pk=pk, buyer=request.user)
            .first()
        )
        if demand is None:
            return Response({"error": "Demande introuvable."}, status=404)
        if demand.packaging_item_id is None:
            return Response({"error": "Ce devis n'est pas lié à une fiche."}, status=400)
        demand.packaging_spec_version = demand.packaging_item.spec_version
        demand.save(update_fields=["packaging_spec_version", "updated_at"])
        demand = _demand_queryset().get(pk=demand.pk)
        return Response(_serialize_demand(demand, request, viewer="buyer"))
