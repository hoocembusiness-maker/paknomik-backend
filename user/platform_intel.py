"""
Aggregated intelligence metrics for the admin panel.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta

from django.db.models import Count, Q
from user.reporting_period import (
    ReportPeriod,
    filter_created_in_local_period,
    month_period,
    trunc_local_day,
)

from user.analytics import (
    MONTH_SHORT,
    _total_platform_searches,
)
from user.models import Event, Product, Supplier
from user.geo_analytics import breakdown_events_by_wilaya

VISIT_EVENT_TYPES = (Event.EventType.BUYER_PRODUCT_VIEW,)

PROFILE_VISIT_EVENT_TYPES = (Event.EventType.BUYER_SUPPLIER_VIEW,)



# Supplier views triggered by a product page load are tracked for supplier
# dashboards but excluded from platform visit totals to avoid double counting.
EMBEDDED_PROFILE_VISIT_SOURCES = {"product_page"}


def _visit_event_qs(start: date, end: date):
    """Platform visits: product views + standalone supplier profile views."""
    qs = Event.objects.filter(
        Q(event_type=Event.EventType.BUYER_PRODUCT_VIEW)
        | (
            Q(event_type=Event.EventType.BUYER_SUPPLIER_VIEW)
            & ~Q(context__source__in=list(EMBEDDED_PROFILE_VISIT_SOURCES))
        )
    )
    return filter_created_in_local_period(qs, start, end)

INTEL_METRICS = {
    "visit": {
        "label": "Visite",
        "event_types": VISIT_EVENT_TYPES + PROFILE_VISIT_EVENT_TYPES,
        "use_visit_queryset": True,
    },
    "search": {
        "label": "Recherche",
        "event_types": (Event.EventType.BUYER_SEARCH,),
        "use_visit_queryset": False,
    },
}


def _parse_period(
    year: int | None = None,
    month: int | None = None,
    period: ReportPeriod | None = None,
) -> ReportPeriod:
    if period is not None:
        return period
    return month_period(year, month, today=date.today())


def _event_qs(event_types: tuple[str, ...], start: date, end: date):
    qs = Event.objects.filter(event_type__in=event_types)
    return filter_created_in_local_period(qs, start, end)


def _event_total(event_types: tuple[str, ...], start: date, end: date) -> int:
    return _event_qs(event_types, start, end).count()


def _daily_counts(event_types: tuple[str, ...], start: date, end: date) -> dict[date, int]:
    rows = (
        _event_qs(event_types, start, end)
        .annotate(day=trunc_local_day("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    return {row["day"]: row["count"] for row in rows}


def _visit_total(start: date, end: date) -> int:
    """Product views + supplier profile visits (excluding embedded product-page views)."""
    return _visit_event_qs(start, end).count()


def _visit_daily_counts(start: date, end: date) -> dict[date, int]:
    rows = (
        _visit_event_qs(start, end)
        .annotate(day=trunc_local_day("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    return {row["day"]: row["count"] for row in rows}


def _daily_series(
    daily_counts: dict[date, int],
    start: date,
    end: date,
) -> tuple[list[str], list[int], list[int]]:
    labels: list[str] = []
    detailed: list[int] = []
    cumulative: list[int] = []
    running = 0

    current = start
    while current <= end:
        value = daily_counts.get(current, 0)
        labels.append(f"{current.day} {MONTH_SHORT[current.month - 1]}")
        detailed.append(value)
        running += value
        cumulative.append(running)
        current += timedelta(days=1)

    return labels, detailed, cumulative


def _daily_mean(values: list[int]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std_dev(values: list[int]) -> int:
    if len(values) < 2:
        return 0
    mean = _daily_mean(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return round(math.sqrt(variance))


def _variation_rate(values: list[int]) -> float:
    """Coefficient of variation: (std dev / daily mean) x 100."""
    mean = _daily_mean(values)
    if mean <= 0:
        return 0.0
    return round(_std_dev(values) / mean * 100, 2)


def _subcategory_label(product: Product) -> str:
    sub = product.subcategory
    if sub:
        return sub.name_fr or sub.name_en or "Autre"
    cat = product.category
    if cat:
        return cat.name_fr or cat.name_en or "Autre"
    return "Autre"


def _pie_rows(counts: Counter[str], *, limit: int = 12) -> list[dict]:
    total = sum(counts.values()) or 1
    return [
        {
            "label": label,
            "count": count,
            "pct": round(count / total * 100, 1),
        }
        for label, count in counts.most_common(limit)
    ]


def _product_visit_breakdown_by_subcategory(start: date, end: date) -> list[dict]:
    """Product detail views only, grouped by subcategory (fallback: main category)."""
    counts: Counter[str] = Counter()
    product_events = list(
        _event_qs((Event.EventType.BUYER_PRODUCT_VIEW,), start, end).values_list(
            "context", flat=True
        )
    )
    product_ids = {
        ctx.get("product_id")
        for ctx in product_events
        if isinstance(ctx, dict) and ctx.get("product_id")
    }
    products = (
        Product.objects.filter(id__in=product_ids).select_related(
            "category", "subcategory"
        )
        if product_ids
        else Product.objects.none()
    )
    labels_by_product = {product.id: _subcategory_label(product) for product in products}

    for ctx in product_events:
        if not isinstance(ctx, dict):
            continue
        product_id = ctx.get("product_id")
        counts[labels_by_product.get(product_id, "Autre")] += 1

    return _pie_rows(counts)


def _supplier_boutique_visit_breakdown(start: date, end: date, *, limit: int = 12) -> list[dict]:
    """Standalone supplier profile / boutique visits (excludes product-page embeds)."""
    qs = _event_qs((Event.EventType.BUYER_SUPPLIER_VIEW,), start, end).exclude(
        context__source__in=list(EMBEDDED_PROFILE_VISIT_SOURCES)
    )
    counts: Counter[str] = Counter()
    supplier_ids: set[int] = set()
    for ctx in qs.values_list("context", flat=True):
        if not isinstance(ctx, dict):
            continue
        supplier_id = ctx.get("supplier_id")
        if supplier_id:
            supplier_ids.add(int(supplier_id))

    if not supplier_ids:
        return []

    suppliers = Supplier.objects.in_bulk(supplier_ids)
    for ctx in qs.values_list("context", flat=True):
        if not isinstance(ctx, dict):
            continue
        supplier_id = ctx.get("supplier_id")
        if not supplier_id:
            continue
        sid = int(supplier_id)
        name = (
            suppliers[sid].company_name
            if sid in suppliers
            else f"Fournisseur #{sid}"
        )
        counts[name] += 1

    return _pie_rows(counts, limit=limit)


def _search_breakdown_by_category(start: date, end: date) -> list[dict]:
    counts: Counter[str] = Counter()
    for ctx in _event_qs(
        (Event.EventType.BUYER_SEARCH,),
        start,
        end,
    ).values_list("context", flat=True):
        if not isinstance(ctx, dict):
            counts["Autre"] += 1
            continue
        category = (ctx.get("category") or "").strip()
        counts[category or "Sans catégorie"] += 1

    total = sum(counts.values()) or 1
    return [
        {
            "label": label,
            "count": count,
            "pct": round(count / total * 100, 1),
        }
        for label, count in counts.most_common()
    ]


def _search_breakdown_by_keyword(start: date, end: date, *, limit: int = 10) -> list[dict]:
    counts: Counter[str] = Counter()
    for ctx in _event_qs(
        (Event.EventType.BUYER_SEARCH,),
        start,
        end,
    ).values_list("context", flat=True):
        if not isinstance(ctx, dict):
            continue
        keyword = (ctx.get("query") or "").strip()
        if keyword:
            counts[keyword] += 1

    if not counts:
        return []

    return [
        {"label": label, "count": count}
        for label, count in counts.most_common(limit)
    ]


def _breakdown_by_visit_supplier(start: date, end: date, *, limit: int = 8) -> list[dict]:
    return _breakdown_by_supplier_from_qs(_visit_event_qs(start, end), limit=limit)


def _breakdown_by_supplier_from_qs(qs, *, limit: int = 8) -> list[dict]:
    counts: Counter[int] = Counter()
    for ctx in qs.values_list("context", flat=True):
        if not isinstance(ctx, dict):
            continue
        supplier_id = ctx.get("supplier_id")
        if supplier_id:
            counts[int(supplier_id)] += 1

    if not counts:
        return []

    suppliers = Supplier.objects.in_bulk(counts.keys())
    rows = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [
        {
            "label": suppliers[supplier_id].company_name
            if supplier_id in suppliers
            else f"Fournisseur #{supplier_id}",
            "count": count,
        }
        for supplier_id, count in rows
    ]


def _breakdown_by_supplier(
    event_types: tuple[str, ...],
    start: date,
    end: date,
    *,
    limit: int = 8,
) -> list[dict]:
    return _breakdown_by_supplier_from_qs(_event_qs(event_types, start, end), limit=limit)


def _breakdown_by_product(start: date, end: date, *, limit: int = 8) -> list[dict]:
    counts: Counter[int] = Counter()
    for ctx in _event_qs(
        (Event.EventType.BUYER_PRODUCT_VIEW,),
        start,
        end,
    ).values_list("context", flat=True):
        if not isinstance(ctx, dict):
            continue
        product_id = ctx.get("product_id")
        if product_id:
            counts[int(product_id)] += 1

    if not counts:
        return []

    products = Product.objects.in_bulk(counts.keys())
    rows = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [
        {
            "label": products[product_id].name
            if product_id in products
            else f"Produit #{product_id}",
            "count": count,
        }
        for product_id, count in rows
    ]


def build_platform_intel_dashboard(
    *,
    year: int | None = None,
    month: int | None = None,
    period: ReportPeriod | None = None,
    metric: str = "visit",
) -> dict:
    metric_key = metric if metric in INTEL_METRICS else "visit"
    metric_cfg = INTEL_METRICS[metric_key]
    event_types = metric_cfg["event_types"]

    period = _parse_period(year, month, period)
    start, end = period.start, period.end

    visit_total = _visit_total(start, end)
    search_total = _total_platform_searches(start, end)

    if metric_key == "visit":
        metric_total = visit_total
        daily = _visit_daily_counts(start, end)
    else:
        metric_total = _event_total(event_types, start, end)
        daily = _daily_counts(event_types, start, end)

    labels, detailed, cumulative = _daily_series(daily, start, end)
    daily_mean = _daily_mean(detailed)
    daily_average = round(daily_mean)
    std_dev = _std_dev(detailed)
    proportion = round(search_total / visit_total * 100) if visit_total else 0
    variation_rate = _variation_rate(detailed)

    product_subcategory_breakdown: list[dict] = []
    supplier_boutique_breakdown: list[dict] = []
    if metric_key == "visit":
        product_subcategory_breakdown = _product_visit_breakdown_by_subcategory(start, end)
        supplier_boutique_breakdown = _supplier_boutique_visit_breakdown(start, end)
        category_breakdown = product_subcategory_breakdown
        search_term_breakdown = []
    else:
        category_breakdown = _search_breakdown_by_category(start, end)
        search_term_breakdown = _search_breakdown_by_keyword(start, end)

    supplier_breakdown = (
        _breakdown_by_visit_supplier(start, end)
        if metric_key == "visit"
        else _breakdown_by_supplier(event_types, start, end)
    )
    product_breakdown = _breakdown_by_product(start, end)

    if metric_key == "visit":
        geo_breakdown = breakdown_events_by_wilaya(_visit_event_qs(start, end))
    else:
        geo_breakdown = breakdown_events_by_wilaya(
            _event_qs(event_types, start, end)
        )

    return {
        "period": period.as_dict(),
        "metric": metric_key,
        "metricLabel": metric_cfg["label"],
        "metrics": [
            {"key": key, "label": cfg["label"]}
            for key, cfg in INTEL_METRICS.items()
        ],
        "kpis": {
            "visits": visit_total,
            "searches": search_total,
            "proportion": proportion,
            "dailyAverage": daily_average,
            "stdDev": std_dev,
            "variationRate": variation_rate,
            "metricTotal": metric_total,
        },
        "chart": {
            "title": metric_cfg["label"],
            "labels": labels,
            "detailed": detailed,
            "cumulative": cumulative,
            "averageLine": daily_average,
        },
        "categoryBreakdown": category_breakdown,
        "productSubcategoryBreakdown": product_subcategory_breakdown,
        "supplierBoutiqueBreakdown": supplier_boutique_breakdown,
        "searchTermBreakdown": search_term_breakdown,
        "supplierBreakdown": supplier_breakdown,
        "productBreakdown": product_breakdown,
        "geoBreakdown": geo_breakdown,
    }
