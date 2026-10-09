"""Supplier response delay.

A buyer asks by sending a message, a shop quote request or a sample request, or by
selecting the supplier on a quote demand. The supplier answers with a message, a quote,
a sent proposal, or by taking a shop quote request or sample request in charge.

Asks and answers are merged per buyer/supplier thread: the first ask after the last
answer starts the clock and the next answer stops it. A request announced by an
automatic buyer message is therefore counted once. Unanswered asks are left out.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.db.models import Min, Q
from django.utils import timezone

from user.models import (
    Message,
    QuoteDemandSelection,
    QuoteProposal,
    QuoteProposalEvent,
    QuoteResponse,
    RFQRequest,
    SampleRequest,
    User,
)

ASK = 0
ANSWER = 1
PUBLIC_WINDOW_DAYS = 180


def _earliest(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _add_request(events: list, asked_at: datetime | None, answered_at: datetime | None) -> None:
    events.append((asked_at, ASK))
    if answered_at is not None:
        events.append((answered_at, ANSWER))


def _threads(supplier_ids: list[int]) -> dict[tuple, list[tuple[datetime | None, int]]]:
    threads: dict[tuple, list[tuple[datetime | None, int]]] = defaultdict(list)

    messages = list(
        Message.objects.filter(supplier_id__in=supplier_ids).values_list(
            "supplier_id", "buyer_id", "sender_id", "created_at"
        )
    )
    sender_supplier = dict(
        User.objects.filter(pk__in={row[2] for row in messages}).values_list("pk", "supplier_id")
    )
    for supplier_id, buyer_id, sender_id, created_at in messages:
        kind = ANSWER if sender_supplier.get(sender_id) == supplier_id else ASK
        threads[(supplier_id, buyer_id)].append((created_at, kind))

    rfqs = (
        RFQRequest.objects.filter(supplier_id__in=supplier_ids)
        .annotate(
            first_quote=Min(
                "responses__created_at",
                filter=~Q(responses__status=QuoteResponse.Status.DRAFT),
            )
        )
        .values_list("pk", "supplier_id", "buyer_id", "created_at", "responded_at", "first_quote")
    )
    for pk, supplier_id, buyer_id, created_at, responded_at, first_quote in rfqs:
        key = (supplier_id, buyer_id) if buyer_id else (supplier_id, f"rfq-{pk}")
        _add_request(threads[key], created_at, _earliest(responded_at, first_quote))

    samples = SampleRequest.objects.filter(supplier_id__in=supplier_ids).values_list(
        "pk", "supplier_id", "buyer_id", "created_at", "responded_at"
    )
    for pk, supplier_id, buyer_id, created_at, responded_at in samples:
        key = (supplier_id, buyer_id) if buyer_id else (supplier_id, f"sample-{pk}")
        _add_request(threads[key], created_at, responded_at)

    selections = QuoteDemandSelection.objects.filter(supplier_id__in=supplier_ids).values_list(
        "supplier_id", "demand__buyer_id", "created_at"
    )
    for supplier_id, buyer_id, created_at in selections:
        threads[(supplier_id, buyer_id)].append((created_at, ASK))

    proposals = (
        QuoteProposal.objects.filter(supplier_id__in=supplier_ids)
        .annotate(
            first_sent=Min(
                "events__created_at",
                filter=Q(events__action=QuoteProposalEvent.Action.SENT),
            )
        )
        .values_list("supplier_id", "demand__buyer_id", "first_sent", "sent_at")
    )
    for supplier_id, buyer_id, first_sent, sent_at in proposals:
        answered_at = first_sent or sent_at
        if answered_at is not None:
            threads[(supplier_id, buyer_id)].append((answered_at, ANSWER))

    return threads


def _in_period(moment: datetime, start: date | None, end: date | None) -> bool:
    day = timezone.localtime(moment).date()
    return (start is None or day >= start) and (end is None or day <= end)


def response_delays(
    supplier_ids, *, start: date | None = None, end: date | None = None
) -> dict[int, list[float]]:
    """Reply delays in minutes per supplier, for asks whose local day is in [start, end]."""
    ids = [int(supplier_id) for supplier_id in supplier_ids]
    delays: dict[int, list[float]] = {supplier_id: [] for supplier_id in ids}
    if not ids:
        return delays
    for (supplier_id, _), events in _threads(ids).items():
        pending = None
        for moment, kind in sorted(event for event in events if event[0] is not None):
            if kind == ANSWER:
                if pending is not None:
                    delays[supplier_id].append((moment - pending).total_seconds() / 60)
                    pending = None
                continue
            if pending is None and _in_period(moment, start, end):
                pending = moment
    return delays


def mean_minutes(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def supplier_response_minutes(supplier_id: int, *, days: int = PUBLIC_WINDOW_DAYS) -> float | None:
    start = timezone.localdate() - timedelta(days=days)
    return mean_minutes(response_delays([supplier_id], start=start)[int(supplier_id)])


def format_response_time(minutes: float | None) -> str | None:
    """Public label, rounded up: « 1h », « 5h », « 2 j »."""
    if minutes is None:
        return None
    hours = max(1, math.ceil(minutes / 60))
    if hours <= 24:
        return f"{hours}h"
    return f"{math.ceil(hours / 24)} j"
