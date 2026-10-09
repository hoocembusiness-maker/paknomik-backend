"""Supplier Pragmadinar consumption dashboard (per wallet, per service, per day).

Consumption = posted SPEND entries. A refunded spend is flipped to REVERSED, so it
drops out on its own; holds only count once they are converted into a spend.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum

from user.platform_config import pragmadinar_rate_dzd, pragmadinar_to_dzd
from user.pragmadinar import LISTING_CLASS_LABELS, format_pragmadinar_amount, wallet_payload
from user.reporting_period import ReportPeriod, filter_created_in_local_period, trunc_local_day

ZERO = Decimal("0")
CATEGORY_ORDER = ("visibility", "analytics", "promotion", "legacy")
OTHER_SERVICE_CODE = "__other__"


def _wallet_labels() -> dict[str, str]:
    from user.models import PragmadinarWallet

    return dict(PragmadinarWallet.Category.choices)


def _consumed_qs(supplier, start, end):
    from user.models import PragmadinarLedger

    qs = PragmadinarLedger.objects.filter(
        wallet__supplier=supplier,
        entry_type=PragmadinarLedger.EntryType.SPEND,
        status=PragmadinarLedger.Status.POSTED,
    )
    return filter_created_in_local_period(qs, start, end)


def consumed_total(supplier, start, end) -> Decimal:
    """Pragmadinar consumed (positive) by ``supplier`` between local dates ``start``/``end``."""
    return _spent(_consumed_qs(supplier, start, end).aggregate(s=Sum("amount"))["s"])


def _refunded_total(supplier, start, end) -> Decimal:
    from user.models import PragmadinarLedger

    qs = PragmadinarLedger.objects.filter(
        wallet__supplier=supplier,
        entry_type=PragmadinarLedger.EntryType.REFUND,
        status=PragmadinarLedger.Status.POSTED,
    )
    return filter_created_in_local_period(qs, start, end).aggregate(s=Sum("amount"))["s"] or ZERO


def _spent(value) -> Decimal:
    return abs(value or ZERO)


def _pct_change(current: Decimal, previous: Decimal):
    if previous == 0:
        return None
    return round(float((current - previous) / previous * 100), 1)


def _share(part: Decimal, total: Decimal):
    if total == 0:
        return None
    return round(float(part / total * 100), 1)


def build_consumption_dashboard(supplier, period: ReportPeriod) -> dict:
    labels = _wallet_labels()
    amount = format_pragmadinar_amount

    current = _consumed_qs(supplier, period.start, period.end)
    previous_total = _spent(
        _consumed_qs(supplier, period.prev_start, period.prev_end).aggregate(s=Sum("amount"))["s"]
    )
    previous_by_category = {
        row["wallet__category"]: _spent(row["s"])
        for row in _consumed_qs(supplier, period.prev_start, period.prev_end)
        .values("wallet__category")
        .annotate(s=Sum("amount"))
    }

    by_service = (
        current.values(
            "wallet__category",
            "service__code",
            "service__label",
            "service__service_class",
        )
        .annotate(s=Sum("amount"), n=Count("id"))
    )
    services_per_category: dict[str, list[dict]] = defaultdict(list)
    consumed_per_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    operations_per_category: dict[str, int] = defaultdict(int)
    for row in by_service:
        category = row["wallet__category"]
        consumed = _spent(row["s"])
        consumed_per_category[category] += consumed
        operations_per_category[category] += row["n"]
        service_class = row["service__service_class"] or ""
        services_per_category[category].append(
            {
                "code": row["service__code"] or OTHER_SERVICE_CODE,
                "label": row["service__label"] or "Autres débits (sans service associé)",
                "serviceClass": service_class,
                "serviceClassLabel": LISTING_CLASS_LABELS.get(service_class, service_class),
                "consumed": consumed,
                "operations": row["n"],
            }
        )

    total = sum(consumed_per_category.values(), ZERO)
    operations = sum(operations_per_category.values())

    daily_rows = (
        current.annotate(day=trunc_local_day())
        .values("day", "wallet__category")
        .annotate(s=Sum("amount"))
    )
    day_category: dict = defaultdict(lambda: defaultdict(lambda: ZERO))
    for row in daily_rows:
        day_category[row["day"]][row["wallet__category"]] += _spent(row["s"])

    days = [period.start + timedelta(days=i) for i in range(period.days)]
    summary = wallet_payload(supplier)
    balances = {w["category"]: w for w in summary["wallets"]}
    categories = [
        c
        for c in CATEGORY_ORDER
        if c in balances or consumed_per_category.get(c, ZERO) > 0 or previous_by_category.get(c, ZERO) > 0
    ]
    categories = [
        c
        for c in categories
        if c != "legacy"
        or consumed_per_category.get(c, ZERO) > 0
        or Decimal(balances.get(c, {}).get("balance", "0")) != 0
    ]

    daily_total = [sum(day_category[d].values(), ZERO) for d in days]
    peak_index = max(range(len(days)), key=lambda i: daily_total[i]) if days else None
    peak = (
        {"date": days[peak_index].isoformat(), "amount": amount(daily_total[peak_index])}
        if peak_index is not None and daily_total[peak_index] > 0
        else None
    )

    wallets = []
    for category in categories:
        consumed = consumed_per_category.get(category, ZERO)
        services = sorted(services_per_category.get(category, []), key=lambda s: s["consumed"], reverse=True)
        balance = balances.get(category, {})
        wallets.append(
            {
                "category": category,
                "label": labels.get(category, category),
                "balance": balance.get("balance", "0.00"),
                "available": balance.get("available", "0.00"),
                "held": balance.get("held", "0.00"),
                "consumed": amount(consumed),
                "previousConsumed": amount(previous_by_category.get(category, ZERO)),
                "change": _pct_change(consumed, previous_by_category.get(category, ZERO)),
                "share": _share(consumed, total),
                "operations": operations_per_category.get(category, 0),
                "services": [
                    {
                        **s,
                        "consumed": amount(s["consumed"]),
                        "share": _share(s["consumed"], consumed),
                    }
                    for s in services
                ],
            }
        )

    return {
        "period": period.as_dict(),
        "rateDzd": str(pragmadinar_rate_dzd()),
        "totals": {
            "consumed": amount(total),
            "consumedDzd": str(pragmadinar_to_dzd(total)),
            "previousConsumed": amount(previous_total),
            "change": _pct_change(total, previous_total),
            "dailyAverage": amount(total / period.days if period.days else ZERO),
            "operations": operations,
            "refunded": amount(_refunded_total(supplier, period.start, period.end)),
            "peakDay": peak,
            "available": summary["available"],
        },
        "wallets": wallets,
        "daily": {
            "dates": [d.isoformat() for d in days],
            "total": [amount(v) for v in daily_total],
            "byCategory": {
                category: [amount(day_category[d].get(category, ZERO)) for d in days]
                for category in categories
            },
        },
    }
