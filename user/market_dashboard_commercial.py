"""
Market Dashboards, Phase 4: commercial activity and engagement across the marketplace.

Built from the business tables (no duplicated analytics data). Supplier ids are only used
to check the distinct-supplier threshold and are never returned.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from django.db import connection
from django.db.models import Min
from django.utils import timezone

from user.market_dashboards import (
    _bucket_key,
    _cached,
    change_pct,
    describe,
    min_population,
    min_suppliers,
    temporal_buckets,
)
from user.market_metrics import GRANULARITY_LABELS, TEMPORAL_GRANULARITIES
from user.models import (
    BuyerFavoriteProduct,
    BuyerFavoriteSupplier,
    Category,
    Message,
    Order,
    QuoteDemand,
    RFQRequest,
    SampleRequest,
)
from user.reporting_period import ReportPeriod, aware_range_for_local_dates

COMMERCIAL_GRANULARITIES = ("day", "week", "month", "weekday", "category")

UNCATEGORIZED = "__none__"
OTHER_CATEGORIES = "__other__"

S = RFQRequest.Status
ORDER_STATUS_GROUPS = (
    (
        "open",
        "En cours",
        (
            Order.Status.PENDING,
            Order.Status.CONFIRMED,
            Order.Status.IN_NEGOTIATION,
            Order.Status.IN_PRODUCTION,
            Order.Status.SHIPPED,
        ),
    ),
    (
        "won",
        "Gagnées, livrées ou terminées",
        (Order.Status.WON, Order.Status.DELIVERED, Order.Status.COMPLETED),
    ),
    (
        "lost",
        "Perdues, annulées ou expirées",
        (Order.Status.LOST, Order.Status.CANCELLED, Order.Status.EXPIRED),
    ),
)
RFQ_STATUS_GROUPS = (
    ("waiting", "En attente de réponse", (S.SUBMITTED, S.IN_REVIEW)),
    ("quoted", "Devis envoyé", (S.QUOTED,)),
    ("accepted", "Acceptées", (S.ACCEPTED,)),
    ("closed", "Refusées ou annulées", (S.REJECTED, S.CANCELLED)),
)


@dataclass
class Activity:
    key: str
    label: str
    group: str
    rows: list[tuple[date, int | None, int | None]] = field(default_factory=list)
    supplier_scoped: bool = True
    tracked: bool = True
    note: str = ""


def _local_date(value) -> date:
    return timezone.localtime(value).date()


def _table_exists(model) -> bool:
    return model._meta.db_table in connection.introspection.table_names()


def _rows(queryset, *fields) -> list[tuple]:
    """(local date, supplier id, category id) from (created_at, supplier, category) fields."""
    result = []
    for values in queryset.values_list(*fields):
        created_at, supplier_id, category_id = (list(values) + [None, None])[:3]
        result.append((_local_date(created_at), supplier_id, category_id))
    return result


def collect_activities(start: date, end: date) -> list[Activity]:
    lo, hi = aware_range_for_local_dates(start, end)
    window = {"created_at__gte": lo, "created_at__lte": hi}
    activities = [
        Activity(
            "rfq",
            "Demandes de devis boutique",
            "commercial",
            _rows(
                RFQRequest.objects.filter(**window),
                "created_at", "supplier_id", "product__category_id",
            ),
        ),
        Activity(
            "quote_demand",
            "Demandes de devis marché",
            "commercial",
            [
                (_local_date(created_at), None, category_id)
                for created_at, category_id in QuoteDemand.objects.filter(**window).values_list(
                    "created_at", "category_id"
                )
            ],
            supplier_scoped=False,
        ),
        Activity(
            "order",
            "Commandes passées sur Paknomik",
            "commercial",
            _rows(
                Order.objects.filter(**window).exclude(origin=Order.Origin.EXTERNAL),
                "created_at", "supplier_id", "product__category_id",
            ),
        ),
    ]
    if _table_exists(SampleRequest):
        activities.append(
            Activity(
                "sample_request",
                "Demandes d'échantillon",
                "commercial",
                _rows(
                    SampleRequest.objects.filter(**window),
                    "created_at", "supplier_id", "product__category_id",
                ),
            )
        )
    else:
        activities.append(
            Activity(
                "sample_request",
                "Demandes d'échantillon",
                "commercial",
                tracked=False,
                note="Fonctionnalité pas encore activée en base de données.",
            )
        )

    first_messages = (
        Message.objects.order_by()
        .values("buyer_id", "supplier_id")
        .annotate(first=Min("created_at"))
        .filter(first__gte=lo, first__lte=hi)
        .values_list("first", "supplier_id")
    )
    activities.extend(
        [
            Activity(
                "conversation",
                "Nouvelles conversations acheteur / fournisseur",
                "engagement",
                [(_local_date(first), supplier_id, None) for first, supplier_id in first_messages],
            ),
            Activity(
                "supplier_favorite",
                "Fournisseurs ajoutés aux favoris",
                "engagement",
                _rows(BuyerFavoriteSupplier.objects.filter(**window), "created_at", "supplier_id"),
            ),
            Activity(
                "product_favorite",
                "Produits ajoutés aux favoris",
                "engagement",
                _rows(
                    BuyerFavoriteProduct.objects.filter(**window),
                    "created_at", "product__supplier_id", "product__category_id",
                ),
            ),
        ]
    )
    return activities


def publishable(rows: list[tuple], supplier_scoped: bool, min_pop: int, min_sup: int) -> bool:
    """Zero is always publishable; otherwise enough distinct suppliers (or items)."""
    if not rows:
        return True
    if supplier_scoped:
        return len({supplier for _, supplier, _ in rows if supplier}) >= min_sup
    return len(rows) >= min_pop


def _category_labels() -> dict[int, str]:
    return {
        pk: (name_fr or name_en or f"Catégorie {pk}")
        for pk, name_fr, name_en in Category.objects.values_list("id", "name_fr", "name_en")
    }


def _category_buckets(activities: list[Activity], min_pop: int) -> tuple[list[dict], set]:
    counts = Counter(
        category for activity in activities for _, _, category in activity.rows if category
    )
    labels = _category_labels()
    kept = {category for category, count in counts.items() if count >= min_pop}
    buckets = [
        {"key": str(category), "label": labels.get(category, f"Catégorie {category}"), "units": 1}
        for category, _ in counts.most_common()
        if category in kept
    ]
    if any(category not in kept for category in counts):
        buckets.append(
            {"key": OTHER_CATEGORIES, "label": f"Autres catégories (< {min_pop})", "units": 1}
        )
    if any(category is None for activity in activities for _, _, category in activity.rows):
        buckets.append({"key": UNCATEGORIZED, "label": "Sans catégorie", "units": 1})
    return buckets, kept


def build_commercial_dashboard(period: ReportPeriod, granularity: str) -> dict:
    return _cached(
        "commercial", period, granularity, lambda: _commercial_dashboard(period, granularity)
    )


def _commercial_dashboard(period: ReportPeriod, granularity: str) -> dict:
    current = collect_activities(period.start, period.end)
    previous = {
        activity.key: activity
        for activity in collect_activities(period.prev_start, period.prev_end)
    }
    min_pop = min_population()
    min_sup = min_suppliers()
    published = {
        activity.key: activity.tracked
        and publishable(activity.rows, activity.supplier_scoped, min_pop, min_sup)
        for activity in current
    }

    if granularity == "category":
        buckets, kept = _category_buckets(
            [activity for activity in current if published[activity.key]], min_pop
        )
    else:
        buckets, kept = temporal_buckets(period.start, period.end, granularity), set()

    def bucket_of(day: date, category) -> str:
        if granularity != "category":
            return _bucket_key(day, granularity)
        if category is None:
            return UNCATEGORIZED
        return str(category) if category in kept else OTHER_CATEGORIES

    per_activity_bucket: dict[str, dict[str, list]] = {}
    for activity in current:
        grouped: dict[str, list] = defaultdict(list)
        for row in activity.rows:
            grouped[bucket_of(row[0], row[2])].append(row)
        per_activity_bucket[activity.key] = grouped

    series = []
    for bucket in buckets:
        values: dict[str, int | None] = {}
        for activity in current:
            if not published[activity.key]:
                values[activity.key] = None
                continue
            rows = per_activity_bucket[activity.key].get(bucket["key"], [])
            if granularity == "category" and not publishable(
                rows, activity.supplier_scoped, min_pop, min_sup
            ):
                values[activity.key] = None
            else:
                values[activity.key] = len(rows)
        series.append({**bucket, "values": values})

    labels = [row["label"] for row in series]
    activities_payload = []
    for activity in current:
        before = previous.get(activity.key)
        is_published = published[activity.key]
        previous_published = bool(
            before
            and before.tracked
            and publishable(before.rows, before.supplier_scoped, min_pop, min_sup)
        )
        total = len(activity.rows) if is_published else None
        previous_total = len(before.rows) if previous_published else None
        bucket_values = [row["values"][activity.key] for row in series]
        visible = [(value, label) for value, label in zip(bucket_values, labels) if value is not None]
        activities_payload.append(
            {
                "key": activity.key,
                "label": activity.label,
                "group": activity.group,
                "tracked": activity.tracked,
                "note": activity.note,
                "published": is_published,
                "total": total,
                "previousTotal": previous_total,
                "changePct": (
                    change_pct(total, previous_total)
                    if total is not None and previous_total is not None
                    else None
                ),
                "perUnit": (
                    describe([value for value, _ in visible], [label for _, label in visible])
                    if is_published
                    else None
                ),
            }
        )

    published_commercial = [
        row for row in activities_payload if row["group"] == "commercial" and row["published"]
    ]
    return {
        "period": period.as_dict(),
        "granularity": granularity,
        "granularityLabel": GRANULARITY_LABELS[granularity],
        "temporal": granularity in TEMPORAL_GRANULARITIES,
        "kpis": {
            "commercialTotal": sum(row["total"] or 0 for row in published_commercial),
            "commercialPublished": len(published_commercial),
            "commercialTracked": sum(
                1 for row in activities_payload if row["group"] == "commercial" and row["tracked"]
            ),
        },
        "activities": activities_payload,
        "series": series,
        "statuses": {
            "orders": _status_breakdown(
                Order.objects.exclude(origin=Order.Origin.EXTERNAL),
                period,
                ORDER_STATUS_GROUPS,
                min_sup,
            ),
            "rfq": _status_breakdown(RFQRequest.objects.all(), period, RFQ_STATUS_GROUPS, min_sup),
        },
        "privacy": {"minSuppliers": min_sup, "minPopulation": min_pop},
    }


def _status_breakdown(queryset, period: ReportPeriod, groups, min_sup: int) -> dict:
    lo, hi = aware_range_for_local_dates(period.start, period.end)
    rows = list(
        queryset.filter(created_at__gte=lo, created_at__lte=hi).values_list("status", "supplier_id")
    )
    if rows and len({supplier for _, supplier in rows if supplier}) < min_sup:
        return {"published": False, "total": None, "rows": []}
    statuses = Counter(status for status, _ in rows)
    total = len(rows)
    return {
        "published": True,
        "total": total,
        "rows": [
            {
                "key": key,
                "label": label,
                "numerator": sum(statuses.get(value, 0) for value in values),
                "denominator": total,
                "ratePct": (
                    round(sum(statuses.get(value, 0) for value in values) / total * 100, 1)
                    if total
                    else 0.0
                ),
            }
            for key, label, values in groups
        ],
    }
