"""
Builds chart data for supplier dashboard (views over time).
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from django.db.models import Count, F, Q, Sum

from user.geo_analytics import breakdown_events_by_country, breakdown_events_by_wilaya
from user.reporting_period import (
    ReportPeriod,
    filter_created_in_local_period,
    month_period,
    trunc_local_day,
)
from user.models import Event, PragmadinarLedger

SUPPLIER_AUDIENCE_EVENT_TYPES = (
    Event.EventType.BUYER_SUPPLIER_VIEW,
    Event.EventType.BUYER_PRODUCT_VIEW,
    Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
)

MONTH_SHORT = [
    "jan",
    "fév",
    "mar",
    "avr",
    "mai",
    "juin",
    "juil",
    "août",
    "sep",
    "oct",
    "nov",
    "déc",
]


def _month_range(year: int, month: int) -> tuple[date, date]:
    month = max(1, min(12, month))
    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _effective_period_end(year: int, month: int) -> date:
    """Cap reporting at today when viewing the current month."""
    _, end = _month_range(year, month)
    today = date.today()
    if year == today.year and month == today.month:
        return min(end, today)
    return end


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _pct_change(current: int, previous: int) -> int:
    if previous == 0:
        return 100 if current > 0 else 0
    return round((current - previous) / previous * 100)


def _supplier_event_qs(
    supplier_id: int,
    event_type: str,
    start: date,
    end: date,
):
    qs = Event.objects.filter(
        event_type=event_type,
        context__supplier_id=supplier_id,
    ).exclude(actor_user__supplier_id=supplier_id)
    return filter_created_in_local_period(qs, start, end)


def _supplier_audience_events_qs(
    supplier_id: int,
    start: date,
    end: date,
):
    """Profile views, product clicks and search appearances from buyers / visitors."""
    qs = Event.objects.filter(
        event_type__in=SUPPLIER_AUDIENCE_EVENT_TYPES,
        context__supplier_id=supplier_id,
    ).exclude(actor_user__supplier_id=supplier_id)
    return filter_created_in_local_period(qs, start, end)


def _supplier_sponsored_views(supplier_id: int, start: date, end: date) -> int:
    """Displays billed in sponsored spaces: campaign hits and paid search / banner placements."""
    qs = PragmadinarLedger.objects.filter(
        wallet__supplier_id=supplier_id,
        entry_type=PragmadinarLedger.EntryType.SPEND,
        status=PragmadinarLedger.Status.POSTED,
    ).filter(
        Q(campaign__isnull=False)
        | Q(service__service_class="promotion", service__unit="view")
    )
    total = filter_created_in_local_period(qs, start, end).aggregate(
        views=Sum("service__unit_quantity")
    )["views"]
    return int(total or 0)


def _supplier_event_count(
    supplier_id: int,
    event_type: str,
    start: date,
    end: date,
) -> int:
    return _supplier_event_qs(supplier_id, event_type, start, end).count()


def _supplier_daily_counts(
    supplier_id: int,
    event_type: str,
    start: date,
    end: date,
) -> dict[date, int]:
    rows = (
        _supplier_event_qs(supplier_id, event_type, start, end)
        .annotate(day=trunc_local_day("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    return {row["day"]: row["count"] for row in rows}


def _chart_sample_dates(start: date, end: date, num_points: int = 7) -> list[date]:
    span = (end - start).days
    if span <= 0:
        return [start]
    if span + 1 <= num_points:
        return [start + timedelta(days=offset) for offset in range(span + 1)]
    step = span / (num_points - 1)
    offsets = sorted({max(0, min(span, int(round(index * step)))) for index in range(num_points)})
    return [start + timedelta(days=offset) for offset in offsets]


def _cumulative_through(daily_counts: dict[date, int], start: date, through: date) -> int:
    return sum(count for day, count in daily_counts.items() if start <= day <= through)


def _resolve_period(year: int | None, month: int | None, period: ReportPeriod | None) -> ReportPeriod:
    if period is not None:
        return period
    return month_period(year, month, today=date.today())


def _total_platform_searches(start: date, end: date) -> int:
    qs = Event.objects.filter(event_type=Event.EventType.BUYER_SEARCH)
    return filter_created_in_local_period(qs, start, end).count()


def _supplier_distinct_search_appearances(
    supplier_id: int,
    start: date,
    end: date,
) -> int:
    """Number of distinct searches where the supplier or their products appeared."""
    search_ids: set[str] = set()
    legacy_keys: set[str] = set()

    appear_qs = Event.objects.filter(
        event_type=Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
        context__supplier_id=supplier_id,
    )
    for context in filter_created_in_local_period(
        appear_qs, start, end
    ).values_list("context", flat=True):
        if not isinstance(context, dict):
            continue
        search_id = context.get("search_id")
        if search_id:
            search_ids.add(str(search_id))
            continue
        legacy_keys.add(
            f"{context.get('search_query', '')}|{context.get('triggered_by_buyer_id') or 'anon'}"
        )

    return len(search_ids) + len(legacy_keys)


def _appearance_rate(
    supplier_search_count: int,
    total_searches: int,
) -> float:
    if total_searches <= 0:
        return 0.0
    return min(round(supplier_search_count / total_searches * 100, 1), 100.0)


def _load_supplier_period_counts(
    supplier_id: int,
    year: int | None = None,
    month: int | None = None,
    *,
    period: ReportPeriod | None = None,
) -> dict:
    period = _resolve_period(year, month, period)
    start, end = period.start, period.end

    appearance_daily = _supplier_daily_counts(
        supplier_id,
        Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
        start,
        end,
    )
    profile_daily = _supplier_daily_counts(
        supplier_id,
        Event.EventType.BUYER_SUPPLIER_VIEW,
        start,
        end,
    )
    product_daily = _supplier_daily_counts(
        supplier_id,
        Event.EventType.BUYER_PRODUCT_VIEW,
        start,
        end,
    )

    search_total = _supplier_distinct_search_appearances(supplier_id, start, end)
    platform_search_total = _total_platform_searches(start, end)
    profile_total = _supplier_event_count(
        supplier_id, Event.EventType.BUYER_SUPPLIER_VIEW, start, end
    )
    product_total = _supplier_event_count(
        supplier_id, Event.EventType.BUYER_PRODUCT_VIEW, start, end
    )

    return {
        "period": period,
        "start": start,
        "end": end,
        "appearance_daily": appearance_daily,
        "profile_daily": profile_daily,
        "product_daily": product_daily,
        "search_total": search_total,
        "platform_search_total": platform_search_total,
        "profile_total": profile_total,
        "product_total": product_total,
    }


def build_supplier_visibility_chart(
    supplier_id: int,
    year: int | None = None,
    month: int | None = None,
    *,
    period: ReportPeriod | None = None,
    period_counts: dict | None = None,
    num_points: int = 7,
) -> dict:
    counts = period_counts or _load_supplier_period_counts(
        supplier_id, year, month, period=period
    )
    start, end = counts["start"], counts["end"]

    sample_dates = _chart_sample_dates(start, end, num_points)
    labels = [f"{day.day} {MONTH_SHORT[day.month - 1]}" for day in sample_dates]

    appearance_cumulative = [
        _supplier_distinct_search_appearances(supplier_id, start, day)
        for day in sample_dates
    ]
    appearance_detailed = [
        _supplier_distinct_search_appearances(supplier_id, day, day)
        for day in sample_dates
    ]
    profile_cumulative = [
        _cumulative_through(counts["profile_daily"], start, day) for day in sample_dates
    ]
    profile_detailed = [counts["profile_daily"].get(day, 0) for day in sample_dates]
    product_cumulative = [
        _cumulative_through(counts["product_daily"], start, day) for day in sample_dates
    ]
    product_detailed = [counts["product_daily"].get(day, 0) for day in sample_dates]

    # Ensure the last cumulative point matches scorecard totals exactly.
    if appearance_cumulative:
        appearance_cumulative[-1] = counts["search_total"]
    if profile_cumulative:
        profile_cumulative[-1] = counts["profile_total"]
    if product_cumulative:
        product_cumulative[-1] = counts["product_total"]
    return {
        "labels": labels,
        "series": [
            {
                "name": "Apparition",
                "detailed": appearance_detailed,
                "cumulative": appearance_cumulative,
            },
            {
                "name": "Visite de profil",
                "detailed": profile_detailed,
                "cumulative": profile_cumulative,
            },
            {
                "name": "Clics produits",
                "detailed": product_detailed,
                "cumulative": product_cumulative,
            },
        ],
        "totals": {
            "Apparition": counts["search_total"],
            "Visite de profil": counts["profile_total"],
            "Clics produits": counts["product_total"],
        },
    }


def build_supplier_visibility_metrics(
    supplier_id: int,
    year: int | None = None,
    month: int | None = None,
    *,
    rank: int,
    total_suppliers: int,
    product_count: int,
    rfq_count: int,
    order_count: int = 0,
    tender_notifications: int,
    period: ReportPeriod | None = None,
    period_counts: dict | None = None,
) -> list[dict]:
    counts = period_counts or _load_supplier_period_counts(
        supplier_id, year, month, period=period
    )
    start = counts["start"]
    end = counts["end"]
    prev_start = counts["period"].prev_start
    prev_end = counts["period"].prev_end

    search_current = counts["search_total"]
    platform_search_current = counts["platform_search_total"]
    profile_current = counts["profile_total"]
    product_clicks_current = counts["product_total"]

    search_previous = _supplier_distinct_search_appearances(
        supplier_id, prev_start, prev_end
    )
    platform_search_previous = _total_platform_searches(prev_start, prev_end)
    profile_previous = _supplier_event_count(
        supplier_id, Event.EventType.BUYER_SUPPLIER_VIEW, prev_start, prev_end
    )
    product_clicks_previous = _supplier_event_count(
        supplier_id, Event.EventType.BUYER_PRODUCT_VIEW, prev_start, prev_end
    )

    market_qs = Event.objects.filter(
        event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
    ).exclude(actor_user__supplier_id=F("context__supplier_id"))
    market_profile_views = filter_created_in_local_period(
        market_qs, start, end
    ).count()
    market_share = (
        round(profile_current / market_profile_views * 100, 1)
        if market_profile_views
        else 0.0
    )
    prev_market_profile_views = filter_created_in_local_period(
        market_qs, prev_start, prev_end
    ).count()
    prev_market_share = (
        round(profile_previous / prev_market_profile_views * 100, 1)
        if prev_market_profile_views
        else 0.0
    )

    appearance_rate = _appearance_rate(search_current, platform_search_current)
    prev_appearance_rate = _appearance_rate(search_previous, platform_search_previous)

    sponsored_current = _supplier_sponsored_views(supplier_id, start, end)
    sponsored_previous = _supplier_sponsored_views(supplier_id, prev_start, prev_end)

    return [
        {
            "label": "Apparition aux résultats de recherches",
            "value": str(search_current),
            "change": _pct_change(search_current, search_previous),
        },
        {
            "label": "Visite de Profil",
            "value": str(profile_current),
            "change": _pct_change(profile_current, profile_previous),
        },
        {
            "label": "Clics sur Produits",
            "value": str(product_clicks_current),
            "change": _pct_change(product_clicks_current, product_clicks_previous),
        },
        {
            "label": "Proportion de visites par rapport aux marché",
            "value": f"{market_share}%",
            "change": _pct_change(int(market_share * 10), int(prev_market_share * 10)),
        },
        {
            "label": "Pourcentage d'apparition",
            "value": f"{appearance_rate}%",
            "change": _pct_change(int(appearance_rate * 10), int(prev_appearance_rate * 10)),
        },
        {
            "key": "sponsored_banner_views",
            "label": "Vues sur bannière sponsorisée",
            "value": str(sponsored_current),
            "change": _pct_change(sponsored_current, sponsored_previous),
        },
    ]


def build_supplier_geo_breakdown(
    supplier_id: int,
    year: int | None = None,
    month: int | None = None,
    *,
    period: ReportPeriod | None = None,
    limit: int = 10,
) -> dict[str, list[dict]]:
    period = _resolve_period(year, month, period)
    qs = _supplier_audience_events_qs(supplier_id, period.start, period.end)
    return {
        "locations": breakdown_events_by_wilaya(qs, limit=limit),
        "countries": breakdown_events_by_country(qs, limit=limit),
    }


def build_supplier_visibility_analytics(
    supplier_id: int,
    year: int | None = None,
    month: int | None = None,
    *,
    period: ReportPeriod | None = None,
    **metric_kwargs,
) -> tuple[list[dict], dict]:
    """Build scorecards and chart from the same period snapshot."""
    period_counts = _load_supplier_period_counts(supplier_id, year, month, period=period)
    metrics = build_supplier_visibility_metrics(
        supplier_id,
        period_counts=period_counts,
        **metric_kwargs,
    )
    chart = build_supplier_visibility_chart(
        supplier_id,
        period_counts=period_counts,
    )
    return metrics, chart
