"""
Paid visibility campaigns (supplier boosts in search or listings).

Suppliers spend Pragmadinar credits for extra views. This file tracks budgets,
impressions, and when to stop showing a campaign.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from user.entitlements import FEATURE_SPONSORED, require_feature
from user.pragmadinar import InsufficientPragmadinar, debit_service, get_service, wallet_payload
from user.platform_config import pragmadinar_to_dzd

FORMAT_SERVICE = {
    "vitrine_extra": "visibility.vitrine_extra",
    "vitrine_central": "visibility.vitrine_central",
    "vitrine_mega": "visibility.vitrine_mega",
    "card_passport": "visibility.card_passport",
    "card_horizontal": "visibility.card_horizontal",
    "banner": "promo.sponsored_banner",
    "banner_conversation": "promo.sponsored_banner_conversation",
}

FORMAT_GROUP = {
    "vitrine_extra": "vitrine",
    "vitrine_central": "vitrine",
    "vitrine_mega": "vitrine",
    "card_passport": "card",
    "card_horizontal": "card",
    "banner": "banner",
    "banner_conversation": "banner",
}

FORMAT_PLACEMENT = {
    "vitrine_extra": "search_results",
    "vitrine_central": "search_results",
    "vitrine_mega": "search_results",
    "card_passport": "search_results",
    "card_horizontal": "search_results",
    "banner": "search_results_bottom",
    "banner_conversation": "buyer_conversations",
}

PLACEMENT_LABELS = {
    "search_results": "Résultats de recherche",
    "search_results_bottom": "Bas des résultats de recherche",
    "buyer_conversations": "Espace conversation acheteur",
}

PLACEMENT_BANNER_FORMAT = {
    "search_results_bottom": "banner",
    "buyer_conversations": "banner_conversation",
}
BANNER_FORMATS = tuple(PLACEMENT_BANNER_FORMAT.values())
BANNER_HIT_DEDUPE_SECONDS = 30 * 60

MAX_DURATION_DAYS = 365

TIER_TO_FORMAT = {
    "extra": "vitrine_extra",
    "central": "vitrine_central",
    "mega": "vitrine_mega",
}

TIER_SEARCH_SERVICE = {
    "extra": "visibility.vitrine_extra_search",
    "central": "visibility.vitrine_central_search",
    "mega": "visibility.vitrine_mega_search",
}

VITRINE_STORE_FORMATS = tuple(TIER_TO_FORMAT.values())
CARD_STORE_FORMATS = ("card_passport", "card_horizontal")

CARD_LAYOUT_FORMAT = {
    "square": "card_passport",
    "horizontal": "card_horizontal",
}


def _flag_enabled(value) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes")


def product_card_base_format(supplier) -> str | None:
    from user.api.serializers import resolved_product_card_layout

    layout = resolved_product_card_layout(supplier)
    if layout == "vertical":
        return None
    return CARD_LAYOUT_FORMAT.get(layout)


def product_card_visit_service_code(supplier) -> str | None:
    from user.api.serializers import get_supplier_product_card_style

    fmt = product_card_base_format(supplier)
    if not fmt:
        return None
    style = get_supplier_product_card_style(supplier) or {}
    colored = _flag_enabled(style.get("useBrandOnCard"))
    if fmt == "card_passport":
        return (
            "visibility.card_passport_colored"
            if colored
            else "visibility.card_passport"
        )
    return (
        "visibility.card_horizontal_colored"
        if colored
        else "visibility.card_horizontal"
    )


def product_card_search_service_code(supplier) -> str | None:
    from user.api.serializers import get_supplier_product_card_style_for_search

    if get_supplier_product_card_style_for_search(supplier) is None:
        return None
    from user.api.serializers import get_supplier_product_card_style

    style = get_supplier_product_card_style(supplier) or {}
    layout = str(style.get("layout") or "vertical").lower()
    colored = _flag_enabled(style.get("useBrandOnCard"))
    if layout == "square":
        return (
            "visibility.card_passport_colored_search"
            if colored
            else "visibility.card_passport_search"
        )
    if layout == "horizontal":
        return (
            "visibility.card_horizontal_colored_search"
            if colored
            else "visibility.card_horizontal_search"
        )
    return None


def campaigns_for(supplier):
    from user.models import Campaign

    return Campaign.objects.filter(supplier=supplier).select_related("service")


def _objective_label(objective: str) -> str:
    from user.models import Campaign

    try:
        return Campaign.Objective(objective).label
    except ValueError:
        return objective


def _goal_progress(campaign) -> int:
    from user.models import Campaign

    if campaign.objective == Campaign.Objective.VISITS:
        return campaign.visits_consumed
    if campaign.objective == Campaign.Objective.VIEWS:
        return campaign.views_consumed
    return campaign.views_consumed + campaign.visits_consumed


def _goal_reached(campaign) -> bool:
    from user.models import Campaign

    if campaign.objective == Campaign.Objective.DURATION or not campaign.view_cap:
        return False
    return _goal_progress(campaign) >= campaign.view_cap


def campaign_payload(campaign, *, organic_views=0) -> dict:
    remaining = campaign.budget_remaining
    return {
        "id": campaign.id,
        "name": campaign.name,
        "format": campaign.campaign_format,
        "formatLabel": campaign.get_campaign_format_display(),
        "formatGroup": FORMAT_GROUP.get(campaign.campaign_format, ""),
        "placement": campaign.placement,
        "placementLabel": PLACEMENT_LABELS.get(campaign.placement, campaign.placement),
        "objective": campaign.objective,
        "objectiveLabel": _objective_label(campaign.objective),
        "goalTarget": campaign.view_cap,
        "goalProgress": _goal_progress(campaign),
        "durationDays": campaign.duration_days,
        "contextScope": campaign.context_scope,
        "contextScopeLabel": campaign.get_context_scope_display(),
        "sponsoredVisits": campaign.visits_consumed,
        "status": campaign.status,
        "statusLabel": campaign.get_status_display(),
        "budgetPragmadinar": str(campaign.budget_pragmadinar),
        "spentPragmadinar": str(campaign.spent_pragmadinar),
        "remainingPragmadinar": str(remaining),
        "budgetDzd": str(pragmadinar_to_dzd(campaign.budget_pragmadinar)),
        "spentDzd": str(pragmadinar_to_dzd(campaign.spent_pragmadinar)),
        "viewCap": campaign.view_cap,
        "sponsoredViews": campaign.views_consumed,
        "organicViews": organic_views,
        "serviceCode": campaign.service.code if campaign.service_id else "",
        "serviceLabel": campaign.service.label if campaign.service_id else "",
        "costLabel": (
            f"{campaign.service.cost_pragmadinar} Pragmadinar / {campaign.service.unit_quantity} "
            f"{campaign.service.unit}"
            if campaign.service_id
            else ""
        ),
        "startsAt": campaign.starts_at,
        "endsAt": campaign.ends_at,
        "createdAt": campaign.created_at,
    }


def organic_views_for(supplier, start, end) -> int:
    from user.models import Event

    qs = Event.objects.filter(context__supplier_id=supplier.pk)
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        qs = qs.filter(created_at__lte=end)
    return qs.filter(
        event_type__in=[
            Event.EventType.BUYER_SUPPLIER_VIEW,
            Event.EventType.BUYER_PRODUCT_VIEW,
            Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
        ]
    ).count()


@transaction.atomic
def create_campaign(supplier, data, *, actor=None):
    from user.models import Campaign

    require_feature(supplier, FEATURE_SPONSORED)
    fmt = data.get("format") or data.get("campaign_format")
    if fmt not in FORMAT_SERVICE:
        raise ValidationError(
            {"error": "Format de campagne inconnu.", "code": "unknown_format"}
        )
    budget = Decimal(str(data.get("budgetPragmadinar") or data.get("budget_pragmadinar") or 0))
    if budget <= 0:
        raise ValidationError({"error": "Le budget doit être positif."})
    objective = (data.get("objective") or "").strip()
    goal_count = _positive_int(
        data.get("goalCount") or data.get("viewCap") or data.get("view_cap")
    )
    duration_days = _positive_int(data.get("durationDays") or data.get("duration_days"))
    if objective == Campaign.Objective.DURATION:
        if not duration_days or duration_days > MAX_DURATION_DAYS:
            raise ValidationError(
                {
                    "error": f"La durée doit être comprise entre 1 et {MAX_DURATION_DAYS} jours.",
                    "code": "invalid_duration",
                }
            )
        goal_count = None
    elif objective in (Campaign.Objective.VISITS, Campaign.Objective.VIEWS):
        if not goal_count:
            raise ValidationError(
                {"error": "Indiquez un nombre de visites ou de vues.", "code": "invalid_goal"}
            )
        duration_days = None
    elif objective:
        raise ValidationError({"error": "Objectif inconnu.", "code": "unknown_objective"})
    context_scope = (
        _validated_context_scope(data.get("contextScope") or data.get("context_scope"))
        if fmt in BANNER_FORMATS
        else Campaign.ContextScope.ALL
    )

    service = get_service(FORMAT_SERVICE[fmt])
    available = Decimal(wallet_payload(supplier, category="promotion")["available"])
    if budget > available:
        raise InsufficientPragmadinar(budget, available)
    default_name = f"{Campaign.Format(fmt).label} — {timezone.localdate():%d/%m/%Y}"
    campaign = Campaign.objects.create(
        supplier=supplier,
        name=((data.get("name") or "").strip() or default_name)[:200],
        campaign_format=fmt,
        placement=FORMAT_PLACEMENT.get(fmt, "")[:64],
        objective=objective,
        service=service,
        budget_pragmadinar=budget,
        view_cap=goal_count,
        duration_days=duration_days,
        context_scope=context_scope,
        status=Campaign.Status.DRAFT,
        starts_at=data.get("startsAt") or data.get("starts_at"),
        ends_at=data.get("endsAt") or data.get("ends_at"),
    )
    return campaign


def _validated_context_scope(value) -> str:
    from user.models import Campaign

    scope = str(value or Campaign.ContextScope.ALL).strip()
    if scope not in Campaign.ContextScope.values:
        raise ValidationError(
            {"error": "Contexte d'affichage inconnu.", "code": "unknown_context_scope"}
        )
    return scope


def set_campaign_context_scope(campaign, value):
    if campaign.campaign_format not in BANNER_FORMATS:
        raise ValidationError(
            {
                "error": "Le contexte d'affichage ne concerne que les bannières.",
                "code": "context_scope_banner_only",
            }
        )
    campaign.context_scope = _validated_context_scope(value)
    campaign.save(update_fields=["context_scope", "updated_at"])
    return campaign


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


@transaction.atomic
def set_campaign_status(campaign, status):
    from user.models import Campaign

    valid = {v for v, _ in Campaign.Status.choices}
    if status not in valid:
        raise ValidationError({"error": "Statut inconnu."})
    fields = ["status", "updated_at"]
    if (
        status == Campaign.Status.ACTIVE
        and campaign.objective == Campaign.Objective.DURATION
        and campaign.duration_days
        and campaign.starts_at is None
    ):
        campaign.starts_at = timezone.now()
        campaign.ends_at = campaign.starts_at + timedelta(days=campaign.duration_days)
        fields += ["starts_at", "ends_at"]
    campaign.status = status
    campaign.save(update_fields=fields)
    return campaign


def store_header_tier(supplier) -> str | None:
    """extra / central / mega when the supplier enabled the store header in search."""
    settings_obj = getattr(supplier, "settings", None)
    if settings_obj is None:
        try:
            settings_obj = supplier.settings
        except Exception:
            return None
    raw = settings_obj.page_design or {}
    flag = raw.get("useStoreHeaderInSearch", False)
    enabled = flag is True or flag is 1 or str(flag).lower() in (
        "true",
        "1",
        "yes",
    )
    if not enabled:
        return None
    tier = str(raw.get("storeHeaderTier") or "standard").lower()
    if tier in TIER_TO_FORMAT:
        return tier
    return None


def search_display_format(supplier) -> str:
    """How a supplier is rendered in search results: vitrine tier, custom card, or standard."""
    tier = store_header_tier(supplier)
    if tier:
        return TIER_TO_FORMAT[tier]
    if product_card_search_service_code(supplier):
        return product_card_base_format(supplier) or "standard"
    return "standard"


def active_vitrine_campaign(supplier_id: int, campaign_format: str):
    from user.models import Campaign

    return (
        Campaign.objects.filter(
            supplier_id=supplier_id,
            status=Campaign.Status.ACTIVE,
            campaign_format=campaign_format,
        )
        .order_by("-updated_at")
        .first()
    )


def bill_vitrine_search_impression(supplier_id: int, *, request=None) -> None:
    """Bill Pragmadinar when the paid store header is shown in search results."""
    from user.events import is_supplier_self_view
    from user.models import Supplier

    if not supplier_id or (request and is_supplier_self_view(request, supplier_id)):
        return
    if is_likely_bot(request):
        return

    supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).select_related(
        "settings"
    ).first()
    if supplier is None:
        return

    tier = store_header_tier(supplier)
    if not tier:
        return

    service_code = TIER_SEARCH_SERVICE.get(tier)
    if not service_code:
        return

    from user.visibility_metering import meter_pragmadinar_vitrine_search_impression

    meter_pragmadinar_vitrine_search_impression(
        supplier, service_code, request=request
    )

    bill_product_card_search_impression(supplier_id, request=request)


def bill_product_card_search_impression(supplier_id: int, *, request=None) -> None:
    """Bill Pragmadinar when a paid product card design is shown in search."""
    from user.events import is_supplier_self_view
    from user.models import Supplier

    if not supplier_id or (request and is_supplier_self_view(request, supplier_id)):
        return
    if is_likely_bot(request):
        return

    supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).select_related(
        "settings"
    ).first()
    if supplier is None:
        return

    service_code = product_card_search_service_code(supplier)
    if not service_code:
        return

    from user.visibility_metering import meter_pragmadinar_vitrine_search_impression

    meter_pragmadinar_vitrine_search_impression(
        supplier, service_code, request=request
    )


def bill_product_card_store_visit(supplier_id: int, *, request=None) -> None:
    """Bill Pragmadinar for a boutique / product view when a paid card layout is active."""
    from user.events import is_supplier_self_view
    from user.models import Campaign, Supplier

    if not supplier_id or (request and is_supplier_self_view(request, supplier_id)):
        return
    if is_likely_bot(request):
        return

    supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
    if supplier is None:
        return

    card_format = product_card_base_format(supplier)
    if not card_format:
        return

    campaign = (
        Campaign.objects.filter(
            supplier_id=supplier_id,
            status=Campaign.Status.ACTIVE,
            campaign_format__in=CARD_STORE_FORMATS,
        )
        .order_by("-updated_at")
        .first()
    )
    if campaign is not None:
        record_sponsored_hit(campaign, kind="visit", request=request)
        return

    service_code = product_card_visit_service_code(supplier)
    if not service_code:
        return

    from user.visibility_metering import meter_pragmadinar_product_card_visit

    meter_pragmadinar_product_card_visit(supplier, service_code, request=request)


def bill_vitrine_store_visit(supplier_id: int, *, request=None) -> None:
    """Bill Pragmadinar for a boutique visit (campaign budget or catalog meter)."""
    from user.events import is_supplier_self_view
    from user.models import Supplier

    if not supplier_id or (request and is_supplier_self_view(request, supplier_id)):
        return
    if is_likely_bot(request):
        return

    supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
    if supplier is None:
        return

    from user.models import Campaign

    campaign = (
        Campaign.objects.filter(
            supplier_id=supplier_id,
            status=Campaign.Status.ACTIVE,
            campaign_format__in=VITRINE_STORE_FORMATS,
        )
        .order_by("-updated_at")
        .first()
    )
    if campaign is not None:
        record_sponsored_hit(campaign, kind="visit", request=request)
        return

    tier = store_header_tier(supplier)
    if not tier:
        return

    campaign_format = TIER_TO_FORMAT[tier]
    from user.visibility_metering import meter_pragmadinar_vitrine_visit

    meter_pragmadinar_vitrine_visit(supplier, campaign_format, request=request)

    bill_product_card_store_visit(supplier_id, request=request)


def _category_parents() -> dict[int, int | None]:
    from user.models import Category

    return dict(Category.objects.values_list("id", "parent_id"))


def _root_category_ids(category_ids, parents: dict[int, int | None]) -> set[int]:
    roots: set[int] = set()
    for category_id in category_ids:
        if category_id not in parents:
            continue
        depth = 0
        while parents.get(category_id) and depth < 5:
            category_id = parents[category_id]
            depth += 1
        roots.add(category_id)
    return roots


def supplier_category_roots(supplier_ids, parents=None) -> dict[int, set[int]]:
    """Root categories a supplier is concerned by: declared categories plus active products."""
    from collections import defaultdict

    from user.models import Product, Supplier

    parents = parents if parents is not None else _category_parents()
    raw: dict[int, set[int]] = defaultdict(set)
    for through in (
        Supplier.interested_categories.through,
        Supplier.interested_subcategories.through,
    ):
        for supplier_id, category_id in through.objects.filter(
            supplier_id__in=supplier_ids
        ).values_list("supplier_id", "category_id"):
            raw[supplier_id].add(category_id)
    for supplier_id, category_id, subcategory_id in Product.objects.filter(
        supplier_id__in=supplier_ids, is_active=True
    ).values_list("supplier_id", "category_id", "subcategory_id"):
        raw[supplier_id].update(cid for cid in (category_id, subcategory_id) if cid)
    return {
        supplier_id: _root_category_ids(ids, parents) for supplier_id, ids in raw.items()
    }


def search_context_roots(*, query="", category="", subcategory_id=None) -> set[int]:
    """Root categories of a search: explicit filters first, otherwise what the query matches."""
    from django.db.models import Q
    from user.models import Category, Product

    parents = _category_parents()
    ids: set[int] = set()
    if subcategory_id:
        ids.add(subcategory_id)
    category = (category or "").strip()
    if category:
        ids.update(
            Category.objects.filter(parent__isnull=True)
            .filter(Q(name_en__icontains=category) | Q(name_fr__iexact=category))
            .values_list("id", flat=True)
        )
    query = (query or "").strip()
    if query and not ids:
        from user.product_search import filter_products_by_text
        from user.search_keyword_resolver import resolve_search_map

        for hit in resolve_search_map(query, limit=5):
            ids.update(cid for cid in (hit.category_id, hit.subcategory_id) if cid)
        matches = filter_products_by_text(
            Product.objects.filter(is_active=True), query, rank_results=False
        ).values_list("category_id", "subcategory_id")[:300]
        for category_id, sub_id in matches:
            ids.update(cid for cid in (category_id, sub_id) if cid)
    return _root_category_ids(ids, parents)


def conversation_context_roots(user) -> set[int]:
    """Root categories of a buyer's conversations and declared interests."""
    from user.models import Message

    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    parents = _category_parents()
    supplier_ids = set(
        Message.objects.filter(buyer=user).values_list("supplier_id", flat=True).distinct()
    )
    roots: set[int] = set()
    for supplier_roots in supplier_category_roots(supplier_ids, parents).values():
        roots |= supplier_roots
    roots |= _root_category_ids(
        user.interested_subcategories.values_list("id", flat=True), parents
    )
    return roots


def pick_banner_campaign(placement: str, *, context_roots=None):
    """One live banner campaign for a public placement (random among eligible), or None.

    `context_roots` is a callable returning the root category ids of the page context. It is
    only evaluated when a campaign is restricted to its supplier's categories.
    """
    import random

    from django.db.models import Q
    from user.models import Campaign

    fmt = PLACEMENT_BANNER_FORMAT.get(placement)
    if not fmt:
        return None
    now = timezone.now()
    live = list(
        Campaign.objects.filter(
            campaign_format=fmt,
            status=Campaign.Status.ACTIVE,
            supplier__is_active=True,
            spent_pragmadinar__lt=F("budget_pragmadinar"),
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .select_related("supplier", "service")
    )
    scoped = Campaign.ContextScope.SUPPLIER_CATEGORIES
    eligible = [c for c in live if c.context_scope != scoped]
    targeted = [c for c in live if c.context_scope == scoped]
    if targeted and context_roots is not None:
        roots = context_roots()
        if roots:
            by_supplier = supplier_category_roots({c.supplier_id for c in targeted})
            eligible += [c for c in targeted if by_supplier.get(c.supplier_id, set()) & roots]
    return random.choice(eligible) if eligible else None


def record_banner_hit(campaign, *, kind: str, request) -> bool:
    """Bill a public banner view/click once per visitor per window; never the owner."""
    from django.core.cache import cache
    from user.events import is_supplier_self_view
    from user.visitor_tracking import actor_dedupe_key

    if kind not in ("view", "visit"):
        kind = "view"
    if is_supplier_self_view(request, campaign.supplier_id):
        return False
    actor = actor_dedupe_key(request, getattr(request, "user", None))
    if not cache.add(
        f"banner-hit:{campaign.id}:{kind}:{actor}", 1, BANNER_HIT_DEDUPE_SECONDS
    ):
        return False
    _, billed = record_sponsored_hit(campaign, kind=kind, request=request)
    return billed


def is_likely_bot(request) -> bool:
    if request is None:
        return False
    from user.visitor_tracking import is_bot_user_agent

    return is_bot_user_agent(request.META.get("HTTP_USER_AGENT") or "")


@transaction.atomic
def record_sponsored_hit(campaign, *, kind="view", request=None, actor=None):
    """Bill one displayed impression/visit. Never double-counts the same call
    when the caller passes an idempotency key via the request header."""
    from user.models import Campaign

    if is_likely_bot(request):
        return campaign, False
    campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
    now = timezone.now()
    if campaign.status != Campaign.Status.ACTIVE:
        return campaign, False
    if campaign.starts_at and now < campaign.starts_at:
        return campaign, False
    if campaign.ends_at and now > campaign.ends_at:
        campaign.status = Campaign.Status.ENDED
        campaign.save(update_fields=["status", "updated_at"])
        return campaign, False
    if _goal_reached(campaign):
        campaign.status = Campaign.Status.COMPLETED
        campaign.save(update_fields=["status", "updated_at"])
        return campaign, False
    if campaign.budget_remaining <= 0:
        campaign.status = Campaign.Status.EXHAUSTED
        campaign.save(update_fields=["status", "updated_at"])
        return campaign, False

    service = campaign.service
    unit_qty = max(int(service.unit_quantity or 1), 1)
    counter = "visits_consumed" if kind == "visit" else "views_consumed"
    next_count = getattr(campaign, counter) + 1
    Campaign.objects.filter(pk=campaign.pk).update(
        **{counter: F(counter) + 1, "updated_at": now}
    )
    campaign.refresh_from_db()
    unit_key = (
        f"campaign:{campaign.id}:visit:unit:{next_count // unit_qty}"
        if kind == "visit"
        else f"campaign:{campaign.id}:unit:{next_count // unit_qty}"
    )

    # Debit one catalog unit every `unit_quantity` displayed hits.
    if next_count % unit_qty == 0:
        try:
            entry = debit_service(
                campaign.supplier,
                service.code,
                wallet_category="promotion",
                ref_type="campaign",
                ref_id=campaign.id,
                reason=f"{campaign.name} — {unit_qty} {kind}s",
                campaign=campaign,
                actor=actor,
                idempotency_key=unit_key,
            )
        except Exception:
            # Roll the view back if we cannot charge: never go negative.
            Campaign.objects.filter(pk=campaign.pk).update(**{counter: F(counter) - 1})
            campaign.refresh_from_db()
            campaign.status = Campaign.Status.EXHAUSTED
            campaign.save(update_fields=["status", "updated_at"])
            return campaign, False
        Campaign.objects.filter(pk=campaign.pk).update(
            spent_pragmadinar=F("spent_pragmadinar") + abs(entry.amount)
        )
        campaign.refresh_from_db()
        if campaign.spent_pragmadinar >= campaign.budget_pragmadinar:
            campaign.status = Campaign.Status.EXHAUSTED
            campaign.save(update_fields=["status", "updated_at"])
    if campaign.status == Campaign.Status.ACTIVE and _goal_reached(campaign):
        campaign.status = Campaign.Status.COMPLETED
        campaign.save(update_fields=["status", "updated_at"])
    return campaign, True
