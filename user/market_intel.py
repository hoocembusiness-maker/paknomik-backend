"""
Market intelligence reports for suppliers (trends, benchmarks).

Builds anonymized stats so one supplier cannot spy on a named competitor.
Some reports are free on Business plans; others cost Pragmadinar credits.
"""

from __future__ import annotations

import calendar
from datetime import date

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from user.platform_config import get_int

# Catalog codes (see user/pragmadinar.py for the prices)
PREVIOUS_MONTH = "intel.dashboard_previous"
PERFORMANCE_COMPARE = "intel.performance_compare"
MARKET_OVERVIEW = "intel.market_overview"
MARKET_CATEGORY = "intel.market_category"
MARKET_PERIOD = "intel.market_period"
BENCHMARK_SUPPLIER = "intel.benchmark_supplier"
MARKET_PERFORMANCE = "intel.market_performance"
REPORT_DETAILED = "intel.report_detailed"
REPORT_MONTHLY = "intel.report_monthly"
REPORT_QUARTERLY = "intel.report_quarterly"
REPORT_SEMIANNUAL = "intel.report_semiannual"
REPORT_ANNUAL = "intel.report_annual"

#: Report code -> (titre, durée en mois). None = période fournie par l'appelant.
REPORT_SPECS = {
    PREVIOUS_MONTH: ("Tableau de bord d'un mois précédent", 1),
    PERFORMANCE_COMPARE: ("Comparatif de performance", 2),
    MARKET_OVERVIEW: ("Analyse de marché — vue d'ensemble", 1),
    MARKET_CATEGORY: ("Analyse de marché — catégorie", 1),
    MARKET_PERIOD: ("Analyse de marché — période", None),
    BENCHMARK_SUPPLIER: ("Benchmarking anonyme", 1),
    MARKET_PERFORMANCE: ("Comparatif de performance du marché", 1),
    REPORT_DETAILED: ("Rapport détaillé de performance", 1),
    REPORT_MONTHLY: ("Rapport mensuel PAKNOMIK", 1),
    REPORT_QUARTERLY: ("Rapport trimestriel PAKNOMIK", 3),
    REPORT_SEMIANNUAL: ("Rapport semestriel PAKNOMIK", 6),
    REPORT_ANNUAL: ("Rapport annuel PAKNOMIK", 12),
}


class NotEnoughData(ValidationError):
    """Raised instead of producing a result that could de-anonymize a peer."""

    def __init__(self, found, required):
        super().__init__(
            {
                "error": (
                    "Données insuffisantes pour produire un résultat anonymisé "
                    f"({found} fournisseur(s) comparables, {required} requis)."
                ),
                "code": "insufficient_data",
                "found": found,
                "required": required,
            }
        )


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def resolve_period(report_type: str, params: dict) -> tuple[date, date]:
    """Period covered by the report. Defaults to the closed previous month."""
    now = timezone.now()
    year = int(params.get("year") or now.year)
    month = int(params.get("month") or now.month)

    spec_months = REPORT_SPECS.get(report_type, (None, 1))[1]
    if spec_months is None:
        start_raw = params.get("startDate")
        end_raw = params.get("endDate")
        if not start_raw or not end_raw:
            raise ValidationError(
                {
                    "error": "Indiquez une date de début et une date de fin.",
                    "code": "period_required",
                }
            )
        start = date.fromisoformat(str(start_raw))
        end = date.fromisoformat(str(end_raw))
        if end < start:
            raise ValidationError({"error": "La période est invalide."})
        return start, end

    if report_type == PREVIOUS_MONTH and not params.get("month"):
        year, month = _shift_month(now.year, now.month, -1)

    end_year, end_month = year, month
    start_year, start_month = _shift_month(year, month, -(spec_months - 1))
    start, _ = _month_bounds(start_year, start_month)
    _, end = _month_bounds(end_year, end_month)
    return start, end


def assert_period_is_closed(report_type: str, start: date, end: date):
    """The current month is free, so it is never sold as a paid report."""
    today = timezone.now().date()
    if report_type == PREVIOUS_MONTH and start.year == today.year and start.month == today.month:
        raise ValidationError(
            {
                "error": (
                    "Le mois en cours est inclus gratuitement dans Paknomik Business."
                ),
                "code": "current_month_is_free",
            }
        )
    if start > today:
        raise ValidationError({"error": "Cette période n'a pas encore commencé."})


def _supplier_event_counts(supplier_id: int, start: date, end: date) -> dict:
    from user.models import Event

    rows = (
        Event.objects.filter(
            created_at__date__gte=start,
            created_at__date__lte=end,
            context__supplier_id=supplier_id,
        )
        .values("event_type")
        .annotate(total=Count("id"))
    )
    counts = {row["event_type"]: row["total"] for row in rows}
    return {
        "profileViews": counts.get(Event.EventType.BUYER_SUPPLIER_VIEW, 0),
        "productViews": counts.get(Event.EventType.BUYER_PRODUCT_VIEW, 0),
        "searchAppearances": counts.get(Event.EventType.SYSTEM_APPEAR_IN_SEARCH, 0),
        "favorites": counts.get(Event.EventType.BUYER_FOLLOW_SUPPLIER, 0)
        + counts.get(Event.EventType.BUYER_PRODUCT_FAVORITE, 0),
    }


def _commercial_counts(supplier_id: int, start: date, end: date) -> dict:
    from user.models import Opportunity, Order, QuoteProposal

    opportunities = Opportunity.objects.filter(
        supplier_id=supplier_id, created_at__date__gte=start, created_at__date__lte=end
    )
    orders = Order.objects.filter(
        supplier_id=supplier_id, created_at__date__gte=start, created_at__date__lte=end
    )
    return {
        "opportunities": opportunities.count(),
        "won": opportunities.filter(stage=Opportunity.Stage.WON).count(),
        "lost": opportunities.filter(stage=Opportunity.Stage.LOST).count(),
        "proposalsSent": QuoteProposal.objects.filter(
            supplier_id=supplier_id,
            created_at__date__gte=start,
            created_at__date__lte=end,
        ).count(),
        "orders": orders.count(),
        "ordersDelivered": orders.filter(status=Order.Status.DELIVERED).count(),
    }


def _peer_supplier_ids(supplier) -> list[int]:
    """Direct competitors (shared product subcategories), excluding the requester."""
    from user.supplier_competitors import competitor_supplier_ids

    return competitor_supplier_ids(supplier)


def _percentile(values: list[float], own: float) -> int:
    if not values:
        return 0
    below = sum(1 for value in values if value < own)
    return round(below / len(values) * 100)


def build_report(supplier, report_type: str, params: dict) -> dict:
    """Produce the report payload. Raises before any debit when impossible."""
    if report_type not in REPORT_SPECS:
        raise ValidationError(
            {"error": "Type de rapport inconnu.", "code": "unknown_report"}
        )

    start, end = resolve_period(report_type, params)
    assert_period_is_closed(report_type, start, end)

    own_visibility = _supplier_event_counts(supplier.pk, start, end)
    own_commercial = _commercial_counts(supplier.pk, start, end)
    base = {
        "reportType": report_type,
        "title": REPORT_SPECS[report_type][0],
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "visibility": own_visibility,
        "commercial": own_commercial,
        "conversionRate": (
            round(own_commercial["won"] / own_commercial["opportunities"] * 100, 1)
            if own_commercial["opportunities"]
            else 0.0
        ),
    }

    if report_type == PERFORMANCE_COMPARE:
        prev_year, prev_month = _shift_month(start.year, start.month, -1)
        prev_start, prev_end = _month_bounds(prev_year, prev_month)
        previous = _supplier_event_counts(supplier.pk, prev_start, prev_end)
        base["comparison"] = {
            "previousPeriod": {
                "start": prev_start.isoformat(),
                "end": prev_end.isoformat(),
            },
            "previous": previous,
            "deltas": {
                key: own_visibility[key] - previous.get(key, 0)
                for key in own_visibility
            },
        }
        return base

    if report_type in {BENCHMARK_SUPPLIER, MARKET_PERFORMANCE}:
        return _build_benchmark(supplier, base, start, end, own_visibility)

    if report_type in {MARKET_OVERVIEW, MARKET_CATEGORY, MARKET_PERIOD}:
        return _build_market(supplier, base, start, end, params)

    # Detailed and periodic reports: own data, broken down month by month.
    months = []
    cursor_year, cursor_month = start.year, start.month
    while (cursor_year, cursor_month) <= (end.year, end.month):
        m_start, m_end = _month_bounds(cursor_year, cursor_month)
        months.append(
            {
                "year": cursor_year,
                "month": cursor_month,
                "visibility": _supplier_event_counts(supplier.pk, m_start, m_end),
                "commercial": _commercial_counts(supplier.pk, m_start, m_end),
            }
        )
        cursor_year, cursor_month = _shift_month(cursor_year, cursor_month, 1)
    base["months"] = months
    return base


def _build_benchmark(supplier, base, start, end, own_visibility) -> dict:
    """Anonymized positioning. Never returns a named competitor."""
    minimum = max(get_int("benchmark_min_suppliers", 5), 2)
    peers = _peer_supplier_ids(supplier)
    if len(peers) < minimum:
        raise NotEnoughData(len(peers), minimum)

    peer_metrics = [_supplier_event_counts(pk, start, end) for pk in peers]
    metrics = {}
    for key, own_value in own_visibility.items():
        values = [row[key] for row in peer_metrics]
        ordered = sorted(values)
        median = (
            ordered[len(ordered) // 2]
            if len(ordered) % 2
            else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
        )
        metrics[key] = {
            "own": own_value,
            "marketAverage": round(sum(values) / len(values), 1),
            "marketMedian": round(median, 1),
            "marketBest": max(values),
            "percentile": _percentile([float(v) for v in values], float(own_value)),
        }

    base["benchmark"] = {
        "anonymized": True,
        "peerCount": len(peers),
        "minimumPeers": minimum,
        "metrics": metrics,
        "notice": (
            "Résultats agrégés et anonymisés : aucun fournisseur comparable "
            "n'est identifiable."
        ),
    }
    return base


def _build_market(supplier, base, start, end, params) -> dict:
    from user.models import Category, Event, QuoteDemand

    minimum = max(get_int("benchmark_min_suppliers", 5), 2)
    demands = QuoteDemand.objects.filter(
        created_at__date__gte=start, created_at__date__lte=end
    )
    category_id = params.get("categoryId")
    category_name = ""
    if category_id:
        category = Category.objects.filter(pk=category_id).first()
        if category is None:
            raise ValidationError({"error": "Catégorie inconnue."})
        category_name = category.name_fr or category.name_en
        demands = demands.filter(
            Q(category_id=category.pk) | Q(subcategory_id=category.pk)
        )

    demand_count = demands.count()
    if demand_count < minimum:
        raise NotEnoughData(demand_count, minimum)

    top_categories = list(
        demands.values("subcategory__name_fr", "subcategory__name_en")
        .annotate(total=Count("id"))
        .order_by("-total")[:10]
    )
    searches = Event.objects.filter(
        event_type=Event.EventType.BUYER_SEARCH,
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).count()

    base["market"] = {
        "anonymized": True,
        "categoryName": category_name,
        "demandCount": demand_count,
        "searchCount": searches,
        "topSubcategories": [
            {
                "name": row["subcategory__name_fr"] or row["subcategory__name_en"],
                "demands": row["total"],
            }
            for row in top_categories
        ],
        "notice": "Agrégats de marché anonymisés, sans identification d'acheteur.",
    }
    return base
