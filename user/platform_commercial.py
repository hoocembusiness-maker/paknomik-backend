"""
Commercial stats for admin dashboards (orders, RFQs, top suppliers).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate

from user.models import (
    BuyerFavoriteProduct,
    BuyerFavoriteSupplier,
    Message,
    Order,
    RFQRequest,
    Supplier,
)
from user.platform_intel import (
    _daily_mean,
    _daily_series,
    _parse_period,
    _std_dev,
    _variation_rate,
)
from user.reporting_period import ReportPeriod
from user.geo_analytics import (
    breakdown_favorites_by_wilaya,
    breakdown_messages_by_wilaya,
    breakdown_orders_by_wilaya,
    merge_geo_breakdown_rows,
)

COMMERCIAL_METRICS = {
    "interactions": {"label": "Interactions"},
    "messages": {"label": "Messages"},
    "orders": {"label": "Commandes"},
    "favorites": {"label": "Favoris"},
}

ORDER_STATUS_LABELS = {
    Order.Status.PENDING: "En attente",
    Order.Status.CONFIRMED: "Confirmée",
    Order.Status.IN_NEGOTIATION: "En négociation",
    Order.Status.WON: "Gagnée",
    Order.Status.LOST: "Perdue",
    Order.Status.IN_PRODUCTION: "En production",
    Order.Status.SHIPPED: "Expédiée",
    Order.Status.DELIVERED: "Livrée",
    Order.Status.COMPLETED: "Terminée",
    Order.Status.CANCELLED: "Annulée",
    Order.Status.EXPIRED: "Expirée",
}


def _period_qs(qs, start: date, end: date):
    return qs.filter(
        created_at__date__gte=start,
        created_at__date__lte=end,
    )


def _messages_in_period(start: date, end: date):
    return _period_qs(Message.objects.all(), start, end)


def _message_read_stats(start: date, end: date) -> dict:
    qs = _messages_in_period(start, end)
    total = qs.count()
    if not total:
        return {
            "total": 0,
            "readCount": 0,
            "readPct": 0.0,
            "buyerSent": 0,
            "supplierSent": 0,
        }

    read_count = qs.filter(
        Q(sender_id=F("buyer_id"), read_by_supplier=True)
        | Q(~Q(sender_id=F("buyer_id")), read_by_buyer=True)
    ).count()
    buyer_sent = qs.filter(sender_id=F("buyer_id")).count()
    supplier_sent = total - buyer_sent

    return {
        "total": total,
        "readCount": read_count,
        "readPct": round(read_count / total * 100, 1),
        "buyerSent": buyer_sent,
        "supplierSent": supplier_sent,
    }


def _orders_in_period(start: date, end: date):
    return _period_qs(Order.objects.select_related("supplier", "product"), start, end)


def _favorites_daily(start: date, end: date) -> dict[date, int]:
    supplier_rows = (
        _period_qs(BuyerFavoriteSupplier.objects.all(), start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    product_rows = (
        _period_qs(BuyerFavoriteProduct.objects.all(), start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    daily: dict[date, int] = {}
    for row in supplier_rows:
        daily[row["day"]] = daily.get(row["day"], 0) + row["count"]
    for row in product_rows:
        daily[row["day"]] = daily.get(row["day"], 0) + row["count"]
    return daily


def _messages_daily(start: date, end: date) -> dict[date, int]:
    rows = (
        _messages_in_period(start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    return {row["day"]: row["count"] for row in rows}


def _orders_daily(start: date, end: date) -> dict[date, int]:
    rows = (
        _orders_in_period(start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    return {row["day"]: row["count"] for row in rows}


def _interactions_daily(start: date, end: date) -> dict[date, int]:
    daily: dict[date, int] = {}
    for source in (
        _messages_daily(start, end),
        _orders_daily(start, end),
        _favorites_daily(start, end),
    ):
        for day, count in source.items():
            daily[day] = daily.get(day, 0) + count
    rfq_rows = (
        _period_qs(RFQRequest.objects.all(), start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    for row in rfq_rows:
        daily[row["day"]] = daily.get(row["day"], 0) + row["count"]
    return daily


def _order_status_breakdown(start: date, end: date) -> list[dict]:
    rows = (
        _orders_in_period(start, end)
        .values("status")
        .annotate(count=Count("id"))
    )
    total = sum(row["count"] for row in rows) or 1
    result = []
    for row in rows:
        status = row["status"]
        count = row["count"]
        result.append(
            {
                "label": ORDER_STATUS_LABELS.get(status, status),
                "count": count,
                "pct": round(count / total * 100, 1),
            }
        )
    result.sort(key=lambda item: item["count"], reverse=True)
    return result


def _top_suppliers_by_orders(start: date, end: date, *, limit: int = 8) -> list[dict]:
    rows = (
        _orders_in_period(start, end)
        .values("supplier_id")
        .annotate(count=Count("id"))
        .order_by("-count")[:limit]
    )
    supplier_ids = [row["supplier_id"] for row in rows]
    suppliers = Supplier.objects.in_bulk(supplier_ids)
    return [
        {
            "label": suppliers[row["supplier_id"]].company_name
            if row["supplier_id"] in suppliers
            else f"Fournisseur #{row['supplier_id']}",
            "count": row["count"],
        }
        for row in rows
    ]


def _top_suppliers_by_messages(start: date, end: date, *, limit: int = 8) -> list[dict]:
    rows = (
        _messages_in_period(start, end)
        .values("supplier_id")
        .annotate(count=Count("id"))
        .order_by("-count")[:limit]
    )
    supplier_ids = [row["supplier_id"] for row in rows]
    suppliers = Supplier.objects.in_bulk(supplier_ids)
    return [
        {
            "label": suppliers[row["supplier_id"]].company_name
            if row["supplier_id"] in suppliers
            else f"Fournisseur #{row['supplier_id']}",
            "count": row["count"],
        }
        for row in rows
    ]


def _interaction_type_breakdown(
    start: date, end: date, msg_stats: dict
) -> list[dict]:
    supplier_fav = _period_qs(BuyerFavoriteSupplier.objects.all(), start, end).count()
    product_fav = _period_qs(BuyerFavoriteProduct.objects.all(), start, end).count()
    orders = _orders_in_period(start, end).count()
    rfqs = _period_qs(RFQRequest.objects.all(), start, end).count()
    rows = [
        {"label": "Messages", "count": msg_stats["total"]},
        {"label": "Commandes", "count": orders},
        {"label": "Favoris fournisseur", "count": supplier_fav},
        {"label": "Favoris produit", "count": product_fav},
        {"label": "Demandes de devis", "count": rfqs},
    ]
    rows = [row for row in rows if row["count"] > 0]
    total = sum(row["count"] for row in rows) or 1
    for row in rows:
        row["pct"] = round(row["count"] / total * 100, 1)
    rows.sort(key=lambda item: item["count"], reverse=True)
    return rows


def build_platform_commercial_dashboard(
    *,
    year: int | None = None,
    month: int | None = None,
    period: ReportPeriod | None = None,
    metric: str = "interactions",
) -> dict:
    metric_key = metric if metric in COMMERCIAL_METRICS else "interactions"
    metric_cfg = COMMERCIAL_METRICS[metric_key]
    period = _parse_period(year, month, period)
    start, end = period.start, period.end

    msg_stats = _message_read_stats(start, end)
    supplier_fav_count = _period_qs(
        BuyerFavoriteSupplier.objects.all(), start, end
    ).count()
    product_fav_count = _period_qs(
        BuyerFavoriteProduct.objects.all(), start, end
    ).count()
    favorites_total = supplier_fav_count + product_fav_count
    orders_qs = _orders_in_period(start, end)
    orders_count = orders_qs.count()
    orders_value = orders_qs.aggregate(
        total=Sum(F("quantity") * F("unit_price"))
    )["total"] or Decimal("0")
    rfq_count = _period_qs(RFQRequest.objects.all(), start, end).count()

    if metric_key == "messages":
        daily = _messages_daily(start, end)
        chart_title = "Messages envoyés par jour"
        metric_total = msg_stats["total"]
        pie_breakdown = [
            {
                "label": "Lus",
                "count": msg_stats["readCount"],
                "pct": msg_stats["readPct"],
            },
            {
                "label": "Non lus",
                "count": msg_stats["total"] - msg_stats["readCount"],
                "pct": round(100 - msg_stats["readPct"], 1)
                if msg_stats["total"]
                else 0,
            },
        ]
        secondary_breakdown = [
            {"label": "Envoyés par acheteurs", "count": msg_stats["buyerSent"]},
            {"label": "Envoyés par fournisseurs", "count": msg_stats["supplierSent"]},
        ]
        supplier_breakdown = _top_suppliers_by_messages(start, end)
    elif metric_key == "orders":
        daily = _orders_daily(start, end)
        chart_title = "Commandes par jour"
        metric_total = orders_count
        pie_breakdown = _order_status_breakdown(start, end)
        secondary_breakdown = []
        supplier_breakdown = _top_suppliers_by_orders(start, end)
    elif metric_key == "favorites":
        daily = _favorites_daily(start, end)
        chart_title = "Favoris ajoutés par jour"
        metric_total = favorites_total
        total_fav = favorites_total or 1
        pie_breakdown = [
            {
                "label": "Fournisseurs",
                "count": supplier_fav_count,
                "pct": round(supplier_fav_count / total_fav * 100, 1),
            },
            {
                "label": "Produits",
                "count": product_fav_count,
                "pct": round(product_fav_count / total_fav * 100, 1),
            },
        ]
        secondary_breakdown = []
        supplier_breakdown = []
    else:
        daily = _interactions_daily(start, end)
        chart_title = "Interactions par jour"
        metric_total = (
            msg_stats["total"] + orders_count + favorites_total + rfq_count
        )
        pie_breakdown = _interaction_type_breakdown(start, end, msg_stats)
        secondary_breakdown = [
            {"label": "Messages lus", "count": msg_stats["readCount"]},
            {"label": "Messages non lus", "count": msg_stats["total"] - msg_stats["readCount"]},
        ]
        supplier_breakdown = _top_suppliers_by_messages(start, end)

    labels, detailed, cumulative = _daily_series(daily, start, end)
    daily_mean = _daily_mean(detailed)
    daily_average = round(daily_mean)

    avg_order_value = (
        round(float(orders_value) / orders_count, 2) if orders_count else 0
    )

    if metric_key == "messages":
        geo_breakdown = breakdown_messages_by_wilaya(start, end)
    elif metric_key == "orders":
        geo_breakdown = breakdown_orders_by_wilaya(start, end)
    elif metric_key == "favorites":
        geo_breakdown = breakdown_favorites_by_wilaya(start, end)
    else:
        geo_breakdown = merge_geo_breakdown_rows(
            breakdown_messages_by_wilaya(start, end),
            breakdown_orders_by_wilaya(start, end),
            breakdown_favorites_by_wilaya(start, end),
        )

    return {
        "period": period.as_dict(),
        "metric": metric_key,
        "metricLabel": metric_cfg["label"],
        "metrics": [
            {"key": key, "label": cfg["label"]}
            for key, cfg in COMMERCIAL_METRICS.items()
        ],
        "kpis": {
            "messagesTotal": msg_stats["total"],
            "messagesReadPct": msg_stats["readPct"],
            "buyerMessagesSent": msg_stats["buyerSent"],
            "supplierMessagesSent": msg_stats["supplierSent"],
            "ordersTotal": orders_count,
            "ordersValue": float(orders_value),
            "avgOrderValue": avg_order_value,
            "favoritesTotal": favorites_total,
            "supplierFavorites": supplier_fav_count,
            "productFavorites": product_fav_count,
            "rfqTotal": rfq_count,
            "dailyAverage": daily_average,
            "stdDev": _std_dev(detailed),
            "variationRate": _variation_rate(detailed),
            "metricTotal": metric_total,
        },
        "chart": {
            "title": chart_title,
            "labels": labels,
            "detailed": detailed,
            "cumulative": cumulative,
            "averageLine": daily_average,
        },
        "typeBreakdown": pie_breakdown,
        "secondaryBreakdown": secondary_breakdown,
        "supplierBreakdown": supplier_breakdown,
        "geoBreakdown": geo_breakdown,
    }
