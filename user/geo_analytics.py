"""
Geographic breakdowns for analytics (wilayas, regions).
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from django.db.models import QuerySet

from user.models import Event, Message, Order, Supplier, User

UNKNOWN_WILAYA_LABEL = "Non renseigné"
DEFAULT_COUNTRY_CODE = "DZ"
DEFAULT_COUNTRY_NAME = "Algérie"

_WILAYA_LABELS = dict(Supplier.WILAYA_CHOICES)


def wilaya_label(code: str | None) -> str:
    if not code:
        return UNKNOWN_WILAYA_LABEL
    return _WILAYA_LABELS.get(code, code.replace("_", " ").title())


def resolve_user_wilaya(user: User | None) -> tuple[str | None, str]:
    if not user:
        return None, UNKNOWN_WILAYA_LABEL

    supplier = getattr(user, "supplier", None)
    if user.supplier_id and supplier and supplier.wilaya:
        return supplier.wilaya, wilaya_label(supplier.wilaya)

    if user.wilaya:
        return user.wilaya, wilaya_label(user.wilaya)

    return None, UNKNOWN_WILAYA_LABEL


def geo_context_for_actor(
    actor_user: User | None = None,
    request=None,
) -> dict[str, str]:
    from user.visitor_tracking import request_analytics_context

    user = actor_user
    if user is None and request is not None:
        req_user = getattr(request, "user", None)
        if req_user and getattr(req_user, "is_authenticated", False):
            user = req_user

    code, label = resolve_user_wilaya(user)
    payload: dict[str, str] = {"geo_wilaya_label": label}
    if code:
        payload["geo_wilaya"] = code
    if user and getattr(user, "is_authenticated", False):
        payload["user_id"] = str(user.id)

    if request is not None:
        for key, value in request_analytics_context(request).items():
            if value not in (None, ""):
                payload[key] = value
        if not code and payload.get("geo_wilaya"):
            payload["geo_wilaya_label"] = wilaya_label(payload["geo_wilaya"])
        elif not code and (payload.get("geo_country") or payload.get("geo_city")):
            payload["geo_wilaya_label"] = _geo_label_from_ip_context(payload)

    if payload.get("geo_wilaya") and not payload.get("geo_country"):
        payload["geo_country"] = DEFAULT_COUNTRY_CODE
        payload.setdefault("geo_country_name", DEFAULT_COUNTRY_NAME)

    return payload


def _geo_label_from_ip_context(ctx: dict) -> str:
    city = (ctx.get("geo_city") or "").strip()
    country = (ctx.get("geo_country_name") or ctx.get("geo_country") or "").strip()
    if city and country:
        return f"{city}, {country}"
    if country:
        return country
    return UNKNOWN_WILAYA_LABEL


def _rows_from_counter(counts: Counter[str], *, limit: int = 12) -> list[dict]:
    total = sum(counts.values()) or 1
    return [
        {
            "label": label,
            "count": count,
            "pct": round(count / total * 100, 1),
        }
        for label, count in counts.most_common(limit)
    ]


def _algeria_country_display() -> str:
    return DEFAULT_COUNTRY_NAME


def event_country_label(context: dict | None, *, actor_user: User | None = None) -> str:
    ctx = context or {}
    name = (ctx.get("geo_country_name") or "").strip()
    if name:
        return name
    code = (ctx.get("geo_country") or "").strip().upper()
    if code == DEFAULT_COUNTRY_CODE:
        return _algeria_country_display()
    if code:
        return code
    wilaya_code = (ctx.get("geo_wilaya") or "").strip()
    if wilaya_code in _WILAYA_LABELS:
        return _algeria_country_display()
    wilaya_label_text = (ctx.get("geo_wilaya_label") or "").strip()
    if wilaya_label_text and wilaya_label_text != UNKNOWN_WILAYA_LABEL:
        if wilaya_label_text in _WILAYA_LABELS.values():
            return _algeria_country_display()
    if actor_user is not None:
        user_wilaya, user_label = resolve_user_wilaya(actor_user)
        if user_wilaya or (
            user_label and user_label != UNKNOWN_WILAYA_LABEL
        ):
            return _algeria_country_display()
    return ""


def event_wilaya_label(event: Event) -> str:
    return _event_wilaya_label(event)


def _event_wilaya_label(event: Event) -> str:
    ctx = event.context or {}
    label = ctx.get("geo_wilaya_label")
    if isinstance(label, str) and label.strip() and label.strip() != UNKNOWN_WILAYA_LABEL:
        return label.strip()
    ip_label = _geo_label_from_ip_context(ctx)
    if ip_label != UNKNOWN_WILAYA_LABEL:
        return ip_label
    _, label = resolve_user_wilaya(event.actor_user)
    return label


def breakdown_events_by_country(
    qs: QuerySet[Event],
    *,
    limit: int = 12,
) -> list[dict]:
    counts: Counter[str] = Counter()
    for event in qs.iterator():
        ctx = event.context or {}
        country = (
            ctx.get("geo_country_name")
            or ctx.get("geo_country")
            or UNKNOWN_WILAYA_LABEL
        )
        counts[str(country)] += 1
    return _rows_from_counter(counts, limit=limit)


def breakdown_events_by_wilaya(
    qs: QuerySet[Event],
    *,
    limit: int = 12,
) -> list[dict]:
    counts: Counter[str] = Counter()
    for event in qs.select_related("actor_user", "actor_user__supplier").iterator():
        counts[_event_wilaya_label(event)] += 1
    return _rows_from_counter(counts, limit=limit)


def breakdown_messages_by_wilaya(start: date, end: date, *, limit: int = 12) -> list[dict]:
    counts: Counter[str] = Counter()
    qs = Message.objects.filter(
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).select_related("sender", "sender__supplier")
    for message in qs.iterator():
        _, label = resolve_user_wilaya(message.sender)
        counts[label] += 1
    return _rows_from_counter(counts, limit=limit)


def breakdown_orders_by_wilaya(start: date, end: date, *, limit: int = 12) -> list[dict]:
    counts: Counter[str] = Counter()
    qs = Order.objects.filter(
        created_at__date__gte=start,
        created_at__date__lte=end,
        buyer__isnull=False,
    ).select_related("buyer", "buyer__supplier")
    for order in qs.iterator():
        _, label = resolve_user_wilaya(order.buyer)
        counts[label] += 1
    return _rows_from_counter(counts, limit=limit)


def breakdown_favorites_by_wilaya(start: date, end: date, *, limit: int = 12) -> list[dict]:
    from user.models import BuyerFavoriteProduct, BuyerFavoriteSupplier

    counts: Counter[str] = Counter()
    supplier_favs = BuyerFavoriteSupplier.objects.filter(
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).select_related("buyer", "buyer__supplier")
    product_favs = BuyerFavoriteProduct.objects.filter(
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).select_related("buyer", "buyer__supplier")

    for favorite in supplier_favs.iterator():
        _, label = resolve_user_wilaya(favorite.buyer)
        counts[label] += 1
    for favorite in product_favs.iterator():
        _, label = resolve_user_wilaya(favorite.buyer)
        counts[label] += 1
    return _rows_from_counter(counts, limit=limit)


def merge_geo_breakdown_rows(*row_lists: list[dict], limit: int = 12) -> list[dict]:
    counts: Counter[str] = Counter()
    for rows in row_lists:
        for row in rows:
            counts[row["label"]] += row["count"]
    return _rows_from_counter(counts, limit=limit)
