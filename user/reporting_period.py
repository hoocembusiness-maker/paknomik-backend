"""Local calendar dates for analytics (Algeria / Django TIME_ZONE)."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import QuerySet
from django.db.models.functions import TruncDate
from django.utils import timezone

MONTH_SHORT_FR = [
    "jan", "fév", "mar", "avr", "mai", "juin",
    "juil", "août", "sep", "oct", "nov", "déc",
]

#: Rolling presets accepted by every dashboard (``?period=``).
PERIOD_PRESETS = ("1d", "7d", "30d", "mtd", "last_month")
PERIOD_MONTH = "month"


@dataclass(frozen=True)
class ReportPeriod:
    """Inclusive local-date window plus the previous window of the same kind."""

    preset: str
    start: date
    end: date
    prev_start: date
    prev_end: date

    @property
    def year(self) -> int:
        return self.end.year

    @property
    def month(self) -> int:
        return self.end.month

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        if self.preset in (PERIOD_MONTH, "last_month"):
            return f"{MONTH_SHORT_FR[self.start.month - 1]} {self.start.year}"
        if self.start == self.end:
            return f"{self.end.day} {MONTH_SHORT_FR[self.end.month - 1]} {self.end.year}"
        start_label = f"{self.start.day} {MONTH_SHORT_FR[self.start.month - 1]}"
        if self.start.year != self.end.year:
            start_label = f"{start_label} {self.start.year}"
        return (
            f"{start_label} – {self.end.day} "
            f"{MONTH_SHORT_FR[self.end.month - 1]} {self.end.year}"
        )

    def as_dict(self) -> dict:
        return {
            "preset": self.preset,
            "year": self.year,
            "month": self.month,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "previousStart": self.prev_start.isoformat(),
            "previousEnd": self.prev_end.isoformat(),
            "days": self.days,
            "label": self.label,
        }


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    month = max(1, min(12, month))
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def month_period(year: int, month: int, *, today: date | None = None) -> ReportPeriod:
    """Calendar month, capped at today; compared with the previous month."""
    today = today or timezone.localdate()
    start, end = _month_bounds(year, month)
    if start <= today <= end:
        end = today
    prev_year, prev_month = _previous_month(start.year, start.month)
    prev_start, prev_end = _month_bounds(prev_year, prev_month)
    if prev_start <= today <= prev_end:
        prev_end = today
    return ReportPeriod(PERIOD_MONTH, start, end, prev_start, prev_end)


def _rolling(preset: str, days: int, today: date) -> ReportPeriod:
    start = today - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return ReportPeriod(preset, start, today, prev_start, prev_end)


def preset_period(preset: str, *, today: date | None = None) -> ReportPeriod:
    today = today or timezone.localdate()
    if preset == "1d":
        return _rolling(preset, 1, today)
    if preset == "7d":
        return _rolling(preset, 7, today)
    if preset == "30d":
        return _rolling(preset, 30, today)
    if preset == "mtd":
        start = today.replace(day=1)
        prev_year, prev_month = _previous_month(today.year, today.month)
        prev_start, prev_last = _month_bounds(prev_year, prev_month)
        prev_end = prev_start.replace(day=min(today.day, prev_last.day))
        return ReportPeriod(preset, start, today, prev_start, prev_end)
    if preset == "last_month":
        prev_year, prev_month = _previous_month(today.year, today.month)
        start, end = _month_bounds(prev_year, prev_month)
        before_year, before_month = _previous_month(prev_year, prev_month)
        prev_start, prev_end = _month_bounds(before_year, before_month)
        return ReportPeriod(preset, start, end, prev_start, prev_end)
    raise ValueError(f"Unknown period preset: {preset}")


def resolve_report_period(params, *, today: date | None = None) -> ReportPeriod:
    """Resolve a custom ``start/end`` range, rolling preset, or legacy calendar month."""
    today = today or timezone.localdate()
    raw_start = (params.get("start") or "").strip()
    raw_end = (params.get("end") or "").strip()
    if raw_start and raw_end:
        try:
            start = date.fromisoformat(raw_start)
            end = date.fromisoformat(raw_end)
        except ValueError:
            start = end = None
        if start and end and start <= end:
            days = (end - start).days + 1
            prev_end = start - timedelta(days=1)
            prev_start = prev_end - timedelta(days=days - 1)
            return ReportPeriod("custom", start, end, prev_start, prev_end)
    preset = (params.get("period") or "").strip().lower()
    if preset in PERIOD_PRESETS:
        return preset_period(preset, today=today)
    try:
        year = int(params.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(params.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month
    year = max(2020, min(2100, year))
    return month_period(year, max(1, min(12, month)), today=today)


def local_tz():
    return timezone.get_current_timezone()


def aware_range_for_local_dates(start: date, end: date) -> tuple[datetime, datetime]:
    tz = local_tz()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end, time.max), tz)
    return start_dt, end_dt


def filter_created_in_local_period(qs: QuerySet, start: date, end: date) -> QuerySet:
    lo, hi = aware_range_for_local_dates(start, end)
    return qs.filter(created_at__gte=lo, created_at__lte=hi)


def trunc_local_day(field_name: str = "created_at"):
    return TruncDate(field_name, tzinfo=local_tz())
