"""Public quote demands: marketplace visibility, expiry, admin queue."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from user.models import QuoteDemand

PUBLIC_MARKET_DAYS = 7


def market_anchor(demand: QuoteDemand):
    return demand.market_published_at or demand.created_at


def initial_market_fields(*, kind: str, has_selections: bool):
    now = timezone.now()
    if kind != QuoteDemand.Kind.PUBLIC:
        return {
            "visible_on_market": False,
            "market_published_at": None,
            "admin_queue": False,
            "market_removed_reason": "",
        }
    if has_selections:
        return {
            "visible_on_market": False,
            "market_published_at": None,
            "admin_queue": False,
            "market_removed_reason": QuoteDemand.MarketRemovedReason.SELECTED_SUPPLIER,
        }
    return {
        "visible_on_market": True,
        "market_published_at": now,
        "admin_queue": False,
        "market_removed_reason": "",
    }


def remove_from_market(demand: QuoteDemand, reason: str, *, admin_queue: bool = False):
    if not demand.visible_on_market and demand.market_removed_reason == reason:
        if admin_queue == demand.admin_queue:
            return False
    demand.visible_on_market = False
    demand.market_removed_reason = reason
    if admin_queue:
        demand.admin_queue = True
    demand.save(
        update_fields=[
            "visible_on_market",
            "market_removed_reason",
            "admin_queue",
            "updated_at",
        ]
    )
    return True


def expire_stale_public_market_listings() -> int:
    """After one week on the market, hand open public demands to Paknomik staff."""
    cutoff = timezone.now() - timedelta(days=PUBLIC_MARKET_DAYS)
    return QuoteDemand.objects.filter(
        kind=QuoteDemand.Kind.PUBLIC,
        status=QuoteDemand.Status.OPEN,
        visible_on_market=True,
        admin_queue=False,
    ).filter(
        Q(market_published_at__lt=cutoff)
        | Q(market_published_at__isnull=True, created_at__lt=cutoff)
    ).update(
        visible_on_market=False,
        admin_queue=True,
        market_removed_reason=QuoteDemand.MarketRemovedReason.AGE_LIMIT,
        updated_at=timezone.now(),
    )


def buyer_can_relaunch(demand: QuoteDemand) -> bool:
    if demand.kind != QuoteDemand.Kind.PUBLIC:
        return False
    if demand.status == QuoteDemand.Status.RESOLVED:
        return False
    return not demand.visible_on_market
