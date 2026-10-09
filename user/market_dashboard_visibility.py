"""
Market Dashboards, Phase 3: marketplace visibility (search appearances and visits).

Market-level only: supplier ids are used to count distinct suppliers for the anonymity
thresholds and are never returned.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date

from django.db.models import Q
from django.utils import timezone

from user.campaigns import FORMAT_GROUP
from user.market_dashboards import (
    UNKNOWN_GEO,
    _bucket_key,
    _buckets_for,
    _cached,
    _geo_bucket_key,
    _market_event_queryset,
    change_pct,
    describe,
    min_suppliers,
)
from user.market_metrics import GRANULARITY_LABELS, TEMPORAL_GRANULARITIES
from user.models import Event, User
from user.reporting_period import ReportPeriod, aware_range_for_local_dates

VISIBILITY_GRANULARITIES = ("day", "week", "month", "weekday", "geo")

DISPLAY_FORMAT_LABELS = {
    "standard": "Affichage standard",
    "card_passport": "Carte produit carrée",
    "card_horizontal": "Carte produit horizontale",
    "vitrine_extra": "Vitrine Extra",
    "vitrine_central": "Vitrine Central",
    "vitrine_mega": "Vitrine Mega",
}

SURFACE_LABELS = {"products": "Résultats produits", "suppliers": "Résultats fournisseurs"}

VISIT_SOURCE_LABELS = {
    ("product", "search"): "Fiche produit, depuis la recherche",
    ("product", "direct"): "Fiche produit, accès direct",
    ("supplier", "direct"): "Boutique, accès direct",
}

#: Visibility formats that exist commercially but are not logged per display.
UNTRACKED_FORMATS = (
    ("banner", "Bannières sponsorisées (bas des résultats, espace conversation)"),
)

TOP_SHARE = 0.2


def _local_date(value) -> date:
    return timezone.localtime(value).date()


def _geo(label) -> str:
    label = (label or "").strip()
    return label or UNKNOWN_GEO


def _appearance_rows(start: date, end: date) -> list[dict]:
    lo, hi = aware_range_for_local_dates(start, end)
    supplier_users = set(User.objects.filter(supplier__isnull=False).values_list("id", flat=True))
    rows = (
        Event.objects.filter(
            event_type=Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
            created_at__gte=lo,
            created_at__lte=hi,
        )
        .exclude(Q(context__has_key="device_class") & Q(context__device_class="bot"))
        .values_list(
            "created_at",
            "context__supplier_id",
            "context__source",
            "context__geo_wilaya_label",
            "context__display_format",
            "context__search_id",
            "context__triggered_by_buyer_id",
        )
    )
    result = []
    for created_at, supplier_id, source, geo, display_format, search_id, buyer_id in rows:
        if buyer_id and buyer_id in supplier_users:
            continue
        result.append(
            {
                "date": _local_date(created_at),
                "supplier": supplier_id,
                "source": source,
                "geo": _geo(geo),
                "format": display_format,
                "search": search_id,
            }
        )
    return result


def _visit_rows(start: date, end: date) -> list[dict]:
    rows = (
        _market_event_queryset(start, end)
        .filter(
            event_type__in=(Event.EventType.BUYER_PRODUCT_VIEW, Event.EventType.BUYER_SUPPLIER_VIEW)
        )
        .exclude(
            Q(event_type=Event.EventType.BUYER_SUPPLIER_VIEW) & Q(context__source="product_page")
        )
        .values_list("created_at", "event_type", "context__source", "context__geo_wilaya_label")
    )
    return [
        {
            "date": _local_date(created_at),
            "kind": "product" if event_type == Event.EventType.BUYER_PRODUCT_VIEW else "supplier",
            "source": source or "direct",
            "geo": _geo(geo),
        }
        for created_at, event_type, source, geo in rows
    ]


def build_visibility_dashboard(period: ReportPeriod, granularity: str) -> dict:
    return _cached(
        "visibility", period, granularity, lambda: _visibility_dashboard(period, granularity)
    )


def _visibility_dashboard(period: ReportPeriod, granularity: str) -> dict:
    appearances = _appearance_rows(period.start, period.end)
    visits = _visit_rows(period.start, period.end)
    previous_appearances = len(_appearance_rows(period.prev_start, period.prev_end))
    previous_visits = len(_visit_rows(period.prev_start, period.prev_end))
    minimum = min_suppliers()

    geo_counts = Counter(row["geo"] for row in appearances + visits)
    buckets = _buckets_for(period, granularity, geo_counts, unit="événements")
    kept_geo = {bucket["key"] for bucket in buckets} if granularity == "geo" else set()

    def bucket_of(row: dict) -> str:
        if granularity == "geo":
            return _geo_bucket_key(row["geo"], kept_geo)
        return _bucket_key(row["date"], granularity)

    appear_per_bucket = Counter(bucket_of(row) for row in appearances)
    product_per_bucket = Counter(bucket_of(row) for row in visits if row["kind"] == "product")
    store_per_bucket = Counter(bucket_of(row) for row in visits if row["kind"] == "supplier")
    series = [
        {
            "key": bucket["key"],
            "label": bucket["label"],
            "units": bucket["units"],
            "appearances": appear_per_bucket.get(bucket["key"], 0),
            "productViews": product_per_bucket.get(bucket["key"], 0),
            "storefrontVisits": store_per_bucket.get(bucket["key"], 0),
        }
        for bucket in buckets
    ]
    labels = [row["label"] for row in series]

    per_search = Counter(row["search"] for row in appearances if row["search"])
    product_appearances = sum(1 for row in appearances if row["source"] == "products")
    search_views = sum(1 for row in visits if row["kind"] == "product" and row["source"] == "search")
    visit_sources = Counter((row["kind"], row["source"]) for row in visits)

    return {
        "period": period.as_dict(),
        "granularity": granularity,
        "granularityLabel": GRANULARITY_LABELS[granularity],
        "temporal": granularity in TEMPORAL_GRANULARITIES,
        "kpis": {
            "appearances": {
                "total": len(appearances),
                "previousTotal": previous_appearances,
                "changePct": change_pct(len(appearances), previous_appearances),
                "perUnit": describe([row["appearances"] for row in series], labels),
            },
            "visits": {
                "total": len(visits),
                "productViews": sum(1 for row in visits if row["kind"] == "product"),
                "storefrontVisits": sum(1 for row in visits if row["kind"] == "supplier"),
                "previousTotal": previous_visits,
                "changePct": change_pct(len(visits), previous_visits),
                "perUnit": describe(
                    [row["productViews"] + row["storefrontVisits"] for row in series], labels
                ),
            },
            "perSearch": {**describe(list(per_search.values())), "searches": len(per_search)},
            "searchVisitRate": {
                "numerator": search_views,
                "denominator": product_appearances,
                "ratePct": (
                    round(search_views / product_appearances * 100, 1)
                    if product_appearances
                    else None
                ),
            },
        },
        "series": series,
        "surfaces": [
            {
                "key": key,
                "label": label,
                "appearances": sum(1 for row in appearances if row["source"] == key),
            }
            for key, label in SURFACE_LABELS.items()
        ],
        "visitSources": [
            {"key": f"{kind}:{source}", "label": _visit_source_label(kind, source), "visits": count}
            for (kind, source), count in visit_sources.most_common()
        ],
        "formats": format_breakdown(appearances, minimum),
        "concentration": concentration(appearances, minimum),
        "privacy": {"minSuppliers": minimum},
        "dataQuality": {
            "appearancesConsidered": len(appearances),
            "appearancesWithFormat": sum(1 for row in appearances if row["format"]),
            "visitsConsidered": len(visits),
        },
    }


def _visit_source_label(kind: str, source: str) -> str:
    if (kind, source) in VISIT_SOURCE_LABELS:
        return VISIT_SOURCE_LABELS[(kind, source)]
    return f"{'Fiche produit' if kind == 'product' else 'Boutique'}, {source}"


def format_breakdown(appearances: list[dict], minimum: int) -> dict:
    """Appearances per display format; formats used by too few suppliers are merged or hidden."""
    by_format: dict[str, list] = defaultdict(list)
    for row in appearances:
        if row["format"]:
            by_format[row["format"]].append(row["supplier"])
    recorded = sum(len(items) for items in by_format.values())

    rows = []
    merged_count = 0
    merged_suppliers: set = set()
    for display_format, suppliers in sorted(by_format.items(), key=lambda item: -len(item[1])):
        distinct = set(suppliers)
        if len(distinct) >= minimum:
            rows.append(
                {
                    "key": display_format,
                    "label": DISPLAY_FORMAT_LABELS.get(display_format, display_format),
                    "group": FORMAT_GROUP.get(display_format, "standard"),
                    "appearances": len(suppliers),
                }
            )
        else:
            merged_count += len(suppliers)
            merged_suppliers |= distinct

    hidden = 0
    if merged_count and len(merged_suppliers) >= minimum:
        rows.append(
            {
                "key": "__other__",
                "label": "Autres formats (regroupés)",
                "group": "other",
                "appearances": merged_count,
            }
        )
    else:
        hidden = merged_count

    for row in rows:
        row["sharePct"] = round(row["appearances"] / recorded * 100, 1) if recorded else 0.0
    return {
        "rows": rows,
        "recorded": recorded,
        "unrecorded": len(appearances) - recorded,
        "hidden": hidden,
        "untracked": [{"key": key, "label": label} for key, label in UNTRACKED_FORMATS],
    }


def concentration(appearances: list[dict], minimum: int) -> dict:
    """Share of appearances captured by the most visible suppliers (no supplier identity)."""
    counts = Counter(row["supplier"] for row in appearances if row["supplier"])
    if len(counts) < minimum:
        return {"published": False, "minimum": minimum}
    total = sum(counts.values())
    top_count = max(1, math.ceil(len(counts) * TOP_SHARE))
    top_total = sum(count for _, count in counts.most_common(top_count))
    values = list(counts.values())
    return {
        "published": True,
        "minimum": minimum,
        "suppliers": len(counts),
        "topSuppliers": top_count,
        "topSharePct": round(top_total / total * 100, 1) if total else 0.0,
        "averagePerSupplier": round(total / len(counts), 1),
        "medianPerSupplier": statistics.median(values),
        "stdDevPerSupplier": round(statistics.pstdev(values), 1),
    }
