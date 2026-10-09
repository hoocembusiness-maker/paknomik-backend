"""
Logs user actions (searches, views, clicks) for analytics
and supplier visibility statistics.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import uuid4

from django.core.cache import cache

from user.models import Event, Supplier, User
from user.geo_analytics import geo_context_for_actor
from user.visitor_tracking import actor_dedupe_key

logger = logging.getLogger(__name__)


def is_supplier_self_view(request, supplier_id: int) -> bool:
    """True when the authenticated user owns the supplier profile being viewed."""
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return user.supplier_id == supplier_id


def log_event(
    event_type: str,
    actor_role: str,
    context: dict[str, Any] | None = None,
    actor_user: User | None = None,
    request=None,
) -> Event:
    """Persist a canonical event row."""
    merged_context = dict(context or {})
    merged_context.update(geo_context_for_actor(actor_user, request))
    return Event.objects.create(
        event_type=event_type,
        actor_role=actor_role,
        actor_user=actor_user,
        context=merged_context,
    )


def log_seller_event(
    user: User,
    event_type: str,
    context: dict[str, Any] | None = None,
    request=None,
) -> Event:
    """Record an event for an authenticated supplier user."""
    return log_event(
        event_type=event_type,
        actor_role=Event.ActorRole.SELLER,
        actor_user=user,
        context=context or {},
        request=request,
    )


def log_buyer_event(
    user: User | None,
    event_type: str,
    context: dict[str, Any] | None = None,
    request=None,
) -> Event:
    """Record a buyer-side event (authenticated buyer or visitor)."""
    role = Event.ActorRole.BUYER if user and user.is_authenticated else Event.ActorRole.VISITOR
    actor = user if user and user.is_authenticated else None
    return log_event(
        event_type=event_type,
        actor_role=role,
        actor_user=actor,
        context=context or {},
        request=request,
    )


def log_system_event(
    event_type: str,
    context: dict[str, Any] | None = None,
    request=None,
) -> Event:
    """Record a platform-generated event."""
    return log_event(
        event_type=event_type,
        actor_role=Event.ActorRole.SYSTEM,
        actor_user=None,
        context=context or {},
        request=request,
    )


def handle_auth_login(user: User, *, method: str = "email") -> None:
    """Log login for buyers; suppliers also update presence score."""
    if user.supplier_id:
        from user.scoring import handle_seller_login

        handle_seller_login(user)
        return

    log_buyer_event(
        user,
        Event.EventType.BUYER_LOGIN,
        {"method": method},
    )


def log_auth_logout(user: User) -> Event:
    """Log logout for buyers and suppliers."""
    if user.supplier_id:
        return log_seller_event(
            user,
            Event.EventType.SELLER_LOGOUT,
            {"supplier_id": user.supplier_id},
        )
    return log_buyer_event(user, Event.EventType.BUYER_LOGOUT, {})


def _search_dedupe_key(request, search_query: str, category: str | None = None) -> str:
    user = request.user if getattr(request.user, "is_authenticated", False) else None
    user_part = actor_dedupe_key(request, user)
    normalized_query = search_query.strip().lower()
    normalized_category = (category or "").strip().lower()
    digest = hashlib.sha256(
        f"{user_part}:{normalized_query}:{normalized_category}".encode()
    ).hexdigest()[:32]
    return f"paknomik:search:{digest}"


def _search_display_formats(supplier_ids: list[int]) -> dict[int, str]:
    from user.campaigns import search_display_format

    formats: dict[int, str] = {}
    try:
        suppliers = Supplier.objects.filter(pk__in=set(supplier_ids)).select_related("settings")
        for supplier in suppliers:
            formats[supplier.pk] = search_display_format(supplier)
    except Exception:
        logger.exception("Search display format lookup failed")
    return formats


def log_search_with_appearances(
    request,
    *,
    search_query: str,
    supplier_ids: list[int],
    results_count: int,
    source: str,
    category: str | None = None,
) -> str | None:
    """Log one platform search and supplier/product appearances within that search."""
    query = search_query.strip()
    if not query:
        return None

    from user.platform_config import search_appearance_dedupe_seconds

    dedupe_key = _search_dedupe_key(request, query, category)
    search_id = cache.get(dedupe_key)
    if not search_id:
        search_id = str(uuid4())
        cache.set(dedupe_key, search_id, timeout=30)
        buyer_id = request.user.id if request.user.is_authenticated else None
        log_buyer_event(
            request.user if request.user.is_authenticated else None,
            Event.EventType.BUYER_SEARCH,
            {
                "query": query,
                "search_id": search_id,
                "results_count": results_count,
                "source": source,
                **({"category": category} if category else {}),
                **({"triggered_by_buyer_id": buyer_id} if buyer_id else {}),
            },
            request=request,
        )
    else:
        search_id = str(search_id)

    user = request.user if getattr(request.user, "is_authenticated", False) else None
    user_part = actor_dedupe_key(request, user)
    normalized_query = query.strip().lower()
    normalized_category = (category or "").strip().lower()
    appearance_dedupe = search_appearance_dedupe_seconds()
    display_formats = _search_display_formats(supplier_ids)

    seen: set[int] = set()
    for position, supplier_id in enumerate(supplier_ids, start=1):
        if supplier_id in seen:
            continue
        seen.add(supplier_id)

        appear_cache_key = (
            f"paknomik:appear:{user_part}:{normalized_category}:"
            f"{normalized_query}:{supplier_id}"
        )
        if appearance_dedupe > 0:
            if cache.get(appear_cache_key):
                continue
            cache.set(appear_cache_key, 1, timeout=appearance_dedupe)

        buyer_id = request.user.id if request.user.is_authenticated else None
        log_system_event(
            Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
            {
                "supplier_id": supplier_id,
                "search_query": query,
                "search_id": search_id,
                "position": position,
                "source": source,
                **(
                    {"display_format": display_formats[supplier_id]}
                    if supplier_id in display_formats
                    else {}
                ),
                **({"triggered_by_buyer_id": buyer_id} if buyer_id else {}),
            },
            request=request,
        )
        from user.visibility_metering import meter_product_search_impression

        meter_product_search_impression(supplier_id, request=request)
    return search_id


def _sync_boutique_visit_to_crm(supplier_id, buyer_id, *, source="direct"):
    try:
        from user import crm

        supplier = Supplier.objects.filter(pk=supplier_id).first()
        buyer = User.objects.filter(pk=buyer_id).first()
        if supplier and buyer and not buyer.supplier_id:
            crm.sync_from_boutique_visit(supplier, buyer, source=source)
    except Exception:
        logger.exception(
            "CRM boutique visit sync failed supplier=%s buyer=%s",
            supplier_id,
            buyer_id,
        )


def _sync_product_view_to_crm(supplier_id, buyer_id, product_id, *, source="direct"):
    try:
        from user import crm
        from user.models import Product

        supplier = Supplier.objects.filter(pk=supplier_id).first()
        buyer = User.objects.filter(pk=buyer_id).first()
        product = Product.objects.filter(pk=product_id).first()
        if supplier and buyer and product and not buyer.supplier_id:
            crm.sync_from_product_view(supplier, buyer, product, source=source)
    except Exception:
        logger.exception(
            "CRM product view sync failed supplier=%s buyer=%s product=%s",
            supplier_id,
            buyer_id,
            product_id,
        )


def log_supplier_search_appearances(
    *,
    supplier_ids: list[int],
    search_query: str,
    buyer_id: int | None = None,
) -> None:
    """Backward-compatible wrapper when only supplier ids are available."""
    del buyer_id
    seen: set[int] = set()
    for position, supplier_id in enumerate(supplier_ids, start=1):
        if supplier_id in seen:
            continue
        seen.add(supplier_id)
        log_system_event(
            Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
            {
                "supplier_id": supplier_id,
                "search_query": search_query,
                "position": position,
                **({"triggered_by_buyer_id": buyer_id} if buyer_id else {}),
            },
        )
        from user.visibility_metering import meter_product_search_impression

        meter_product_search_impression(supplier_id, request=None)


def _view_dedupe_key(request, *, kind: str, target_id: int) -> str:
    user = request.user if getattr(request.user, "is_authenticated", False) else None
    user_part = actor_dedupe_key(request, user)
    digest = hashlib.sha256(f"{user_part}:{kind}:{target_id}".encode()).hexdigest()[:32]
    return f"paknomik:view:{digest}"


def _should_log_view(request, cache_key: str, *, timeout: int | None = None) -> bool:
    if timeout is None:
        from user.platform_config import visit_dwell_seconds

        timeout = visit_dwell_seconds()
    if cache.get(cache_key):
        return False
    cache.set(cache_key, 1, timeout=timeout)
    return True


def log_supplier_profile_view(
    request,
    supplier_id: int,
    *,
    source: str = "direct",
) -> None:
    """Log a supplier profile view from the public profile page."""
    if is_supplier_self_view(request, supplier_id):
        return

    cache_key = _view_dedupe_key(request, kind="supplier", target_id=supplier_id)
    if not _should_log_view(request, cache_key):
        return

    buyer_id = request.user.id if request.user.is_authenticated else None
    log_buyer_event(
        request.user if request.user.is_authenticated else None,
        Event.EventType.BUYER_SUPPLIER_VIEW,
        {
            "supplier_id": supplier_id,
            "source": source,
        },
        request=request,
    )
    if buyer_id:
        log_system_event(
            Event.EventType.SELLER_PROFILE_VIEW_RECEIVED,
            {"supplier_id": supplier_id, "viewer_id": buyer_id, "source": source},
        )
        if source != "product_page":
            _sync_boutique_visit_to_crm(supplier_id, buyer_id, source=source)

    if source != "product_page":
        from user.visibility_metering import meter_store_page_visit

        meter_store_page_visit(supplier_id, request=request)


def log_supplier_product_view(
    request,
    *,
    supplier_id: int,
    product_id: int,
    source: str = "direct",
) -> None:
    """Log a product detail view."""
    if is_supplier_self_view(request, supplier_id):
        return

    cache_key = _view_dedupe_key(request, kind="product", target_id=product_id)
    if not _should_log_view(request, cache_key):
        return

    log_buyer_event(
        request.user if request.user.is_authenticated else None,
        Event.EventType.BUYER_PRODUCT_VIEW,
        {
            "supplier_id": supplier_id,
            "product_id": product_id,
            "source": source,
        },
        request=request,
    )
    buyer_id = request.user.id if request.user.is_authenticated else None
    if buyer_id:
        _sync_product_view_to_crm(
            supplier_id,
            buyer_id,
            product_id,
            source=source,
        )
    from user.visibility_metering import meter_product_page_visit

    meter_product_page_visit(supplier_id, request=request)
    log_supplier_profile_view(
        request,
        supplier_id,
        source="product_page",
    )


def _compare_dedupe_key(request, product_ids: list[int]) -> str:
    user = request.user if getattr(request.user, "is_authenticated", False) else None
    user_part = actor_dedupe_key(request, user)
    ids_part = ",".join(str(pid) for pid in sorted(product_ids))
    digest = hashlib.sha256(f"{user_part}:compare:{ids_part}".encode()).hexdigest()[:32]
    return f"paknomik:compare:{digest}"


def log_product_compare(
    request,
    products,
) -> None:
    """Log a product comparison session (buyer.compare)."""
    product_ids = [product.id for product in products]
    if len(product_ids) < 2:
        return

    cache_key = _compare_dedupe_key(request, product_ids)
    if not _should_log_view(request, cache_key, timeout=30):
        return

    user = request.user if getattr(request.user, "is_authenticated", False) else None
    log_buyer_event(
        user if user and user.is_authenticated else None,
        Event.EventType.BUYER_COMPARE,
        {
            "items_type": "product",
            "items_ids": product_ids,
            "products": [
                {"id": product.id, "name": product.name}
                for product in products
            ],
            "user_id": user.id if user and user.is_authenticated else None,
        },
        request=request,
    )
