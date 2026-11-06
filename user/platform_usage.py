"""
Usage counters and summaries for platform monitoring.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Count, Max, Min
from django.db.models.functions import ExtractHour, TruncDate

from user.models import Event
from user.reporting_period import ReportPeriod
from user.platform_intel import (
    _daily_mean,
    _daily_series,
    _parse_period,
    _std_dev,
    _variation_rate,
)
from user.geo_analytics import breakdown_events_by_wilaya

USAGE_ROLES = {
    "buyer": {
        "label": "Acheteur",
        "actor_role": Event.ActorRole.BUYER,
    },
    "supplier": {
        "label": "Fournisseur",
        "actor_role": Event.ActorRole.SELLER,
    },
}

HOUR_LABELS = [f"{hour:02d}h" for hour in range(24)]


def _role_event_qs(role: str, start: date, end: date):
    role_key = role if role in USAGE_ROLES else "buyer"
    actor_role = USAGE_ROLES[role_key]["actor_role"]
    return Event.objects.filter(
        actor_role=actor_role,
        actor_user_id__isnull=False,
        created_at__date__gte=start,
        created_at__date__lte=end,
    )


def _daily_active_users(role: str, start: date, end: date) -> dict[date, int]:
    rows = (
        _role_event_qs(role, start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(users=Count("actor_user", distinct=True))
    )
    return {row["day"]: row["users"] for row in rows}


def _avg_usage_minutes_per_day(role: str, start: date, end: date) -> float:
    """Average active window (first→last event) per user-day, in minutes."""
    user_days = (
        _role_event_qs(role, start, end)
        .annotate(day=TruncDate("created_at"))
        .values("day", "actor_user_id")
        .annotate(first=Min("created_at"), last=Max("created_at"))
    )
    total_minutes = 0.0
    count = 0
    for row in user_days:
        first = row["first"]
        last = row["last"]
        if not first or not last:
            continue
        delta_minutes = max((last - first).total_seconds() / 60, 1.0)
        total_minutes += delta_minutes
        count += 1
    if not count:
        return 0.0
    return round(total_minutes / count, 1)


def _hourly_distribution(role: str, start: date, end: date) -> list[dict]:
    counts = {hour: 0 for hour in range(24)}
    rows = (
        _role_event_qs(role, start, end)
        .annotate(hour=ExtractHour("created_at"))
        .values("hour")
        .annotate(count=Count("id"))
    )
    for row in rows:
        hour = int(row["hour"])
        if 0 <= hour <= 23:
            counts[hour] = row["count"]

    total = sum(counts.values()) or 1
    return [
        {
            "label": HOUR_LABELS[hour],
            "count": counts[hour],
            "pct": round(counts[hour] / total * 100, 1),
        }
        for hour in range(24)
    ]


def _peak_hour_label(hourly_rows: list[dict]) -> str:
    if not hourly_rows:
        return "—"
    peak = max(hourly_rows, key=lambda row: row["count"])
    if peak["count"] <= 0:
        return "—"
    hour = int(peak["label"][:2])
    return f"{hour:02d}h–{(hour + 1) % 24:02d}h"


def build_platform_usage_dashboard(
    *,
    year: int | None = None,
    month: int | None = None,
    period: ReportPeriod | None = None,
    role: str = "buyer",
) -> dict:
    role_key = role if role in USAGE_ROLES else "buyer"
    role_cfg = USAGE_ROLES[role_key]
    period = _parse_period(year, month, period)
    start, end = period.start, period.end

    daily_users = _daily_active_users(role_key, start, end)
    labels, detailed, cumulative = _daily_series(daily_users, start, end)

    total_active_users = (
        _role_event_qs(role_key, start, end)
        .values("actor_user_id")
        .distinct()
        .count()
    )
    daily_mean = _daily_mean(detailed)
    daily_average = round(daily_mean)
    std_dev = _std_dev(detailed)
    variation_rate = _variation_rate(detailed)
    avg_usage_minutes = _avg_usage_minutes_per_day(role_key, start, end)
    hourly_rows = _hourly_distribution(role_key, start, end)
    peak_hour = _peak_hour_label(hourly_rows)

    login_event = (
        Event.EventType.BUYER_LOGIN
        if role_key == "buyer"
        else Event.EventType.SELLER_LOGIN
    )
    login_count = Event.objects.filter(
        event_type=login_event,
        actor_role=role_cfg["actor_role"],
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).count()

    geo_breakdown = breakdown_events_by_wilaya(_role_event_qs(role_key, start, end))

    return {
        "period": period.as_dict(),
        "role": role_key,
        "roleLabel": role_cfg["label"],
        "roles": [{"key": key, "label": cfg["label"]} for key, cfg in USAGE_ROLES.items()],
        "kpis": {
            "activeUsers": total_active_users,
            "dailyAverageUsers": daily_average,
            "avgUsageMinutes": avg_usage_minutes,
            "peakHour": peak_hour,
            "stdDev": std_dev,
            "variationRate": variation_rate,
            "loginCount": login_count,
        },
        "chart": {
            "title": "Utilisateurs actifs par jour",
            "labels": labels,
            "detailed": detailed,
            "cumulative": cumulative,
            "averageLine": daily_average,
        },
        "hourlyBreakdown": hourly_rows,
        "geoBreakdown": geo_breakdown,
    }
