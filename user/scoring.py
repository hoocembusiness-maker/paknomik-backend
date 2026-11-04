"""
Computes supplier scores and ranks from logged events.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Literal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from user.events import log_buyer_event, log_seller_event, log_system_event
from user.models import Event, Message, Supplier, SupplierScore, User

logger = logging.getLogger(__name__)

BASE_SCORE = 50

ScoreCategory = Literal["presence", "responsivity", "catalog", "profile", "commercial"]

SUBSCORE_FIELDS: dict[ScoreCategory, str] = {
    "presence": "score_presence",
    "responsivity": "score_responsivity",
    "catalog": "score_catalog",
    "profile": "score_profile",
    "commercial": "score_commercial",
}


def _refresh_total(score: SupplierScore) -> None:
    score.score_total = (
        BASE_SCORE
        + score.score_presence
        + score.score_responsivity
        + score.score_catalog
        + score.score_profile
        + score.score_commercial
    )


def _emit_score_updated(score: SupplierScore, reason: str, delta: int) -> None:
    log_system_event(
        Event.EventType.SYSTEM_SCORE_UPDATED,
        {
            "supplier_id": score.supplier_id,
            "reason": reason,
            "delta": delta,
            "score_total": score.score_total,
            "score_presence": score.score_presence,
            "score_responsivity": score.score_responsivity,
            "score_catalog": score.score_catalog,
            "score_profile": score.score_profile,
            "score_commercial": score.score_commercial,
        },
    )


@transaction.atomic
def apply_score_delta(
    supplier_id: int,
    category: ScoreCategory,
    delta: int,
    reason: str,
    *,
    actor_user: User | None = None,
    extra_context: dict[str, Any] | None = None,
) -> SupplierScore:
    """Apply a points change to one sub-score category and refresh the total."""
    if delta == 0:
        score, _ = SupplierScore.objects.select_for_update().get_or_create(
            supplier_id=supplier_id,
            defaults={"score_total": BASE_SCORE},
        )
        return score

    score = (
        SupplierScore.objects.select_for_update()
        .select_related("supplier")
        .get_or_create(supplier_id=supplier_id, defaults={"score_total": BASE_SCORE})[0]
    )
    field = SUBSCORE_FIELDS[category]
    setattr(score, field, getattr(score, field) + delta)
    _refresh_total(score)
    score.save(
        update_fields=[
            field,
            "score_total",
            "updated_at",
        ]
    )
    _emit_score_updated(score, reason, delta)

    if actor_user and actor_user.supplier_id == supplier_id:
        log_seller_event(
            actor_user,
            _reason_to_seller_event(reason),
            {
                "supplier_id": supplier_id,
                "reason": reason,
                "delta": delta,
                "score_total": score.score_total,
                **(extra_context or {}),
            },
        )

    return score


def _reason_to_seller_event(reason: str) -> str:
    mapping = {
        "product_added": Event.EventType.SELLER_PRODUCT_ADDED,
        "product_updated": Event.EventType.SELLER_PRODUCT_UPDATED,
        "product_deleted": Event.EventType.SELLER_PRODUCT_DELETED,
        "profile_updated": Event.EventType.SELLER_PROFILE_UPDATED,
        "message_reply_same_day": Event.EventType.SELLER_MESSAGE_REPLY,
        "seller_login": Event.EventType.SELLER_LOGIN,
    }
    return mapping.get(reason, Event.EventType.SELLER_PROFILE_UPDATED)


def ensure_supplier_score(supplier_id: int) -> SupplierScore:
    score, _ = SupplierScore.objects.get_or_create(
        supplier_id=supplier_id,
        defaults={"score_total": BASE_SCORE},
    )
    return score


def get_supplier_rank(supplier_id: int) -> tuple[int, int]:
    """Return (rank, total_active_suppliers) based on score_total."""
    active_supplier_ids = Supplier.objects.filter(is_active=True, is_ghost=False).values_list("id", flat=True)
    total = len(active_supplier_ids)
    if not total:
        return 0, 0

    try:
        current = SupplierScore.objects.get(supplier_id=supplier_id)
        current_total = current.score_total
    except SupplierScore.DoesNotExist:
        current_total = BASE_SCORE

    better = SupplierScore.objects.filter(
        supplier_id__in=active_supplier_ids,
    ).filter(
        Q(score_total__gt=current_total)
        | Q(score_total=current_total, supplier_id__lt=supplier_id)
    ).count()
    return better + 1, total


def _profile_bonus_key(field: str) -> str:
    return f"profile_{field}"


def _has_profile_bonus(score: SupplierScore, field: str) -> bool:
    return bool(score.profile_bonuses_awarded.get(_profile_bonus_key(field)))


def _mark_profile_bonus(score: SupplierScore, field: str) -> None:
    bonuses = dict(score.profile_bonuses_awarded or {})
    bonuses[_profile_bonus_key(field)] = True
    score.profile_bonuses_awarded = bonuses
    score.save(update_fields=["profile_bonuses_awarded", "updated_at"])


def _supplier_has_location(supplier: Supplier) -> bool:
    return bool(supplier.wilaya or (supplier.address or "").strip())


def award_profile_bonuses_for_supplier(
    supplier: Supplier,
    *,
    actor_user: User | None = None,
) -> None:
    """One-time profile completeness bonuses."""
    score = ensure_supplier_score(supplier.id)
    checks: list[tuple[str, bool, int]] = [
        ("email", bool(supplier.email), 10),
        ("website", bool(supplier.website), 10),
        ("phone", bool(supplier.whatsapp_number), 15),
        ("location", _supplier_has_location(supplier), 15),
    ]
    for field, is_filled, points in checks:
        if not is_filled or _has_profile_bonus(score, field):
            continue
        apply_score_delta(
            supplier.id,
            "profile",
            points,
            f"profile_{field}_filled",
            actor_user=actor_user,
            extra_context={"field": field},
        )
        score.refresh_from_db()
        _mark_profile_bonus(score, field)


def handle_supplier_created(supplier: Supplier, *, actor_user: User | None = None) -> None:
    from user.pragmadinar import grant_supplier_welcome_pragmadinar

    ensure_supplier_score(supplier.id)
    award_profile_bonuses_for_supplier(supplier, actor_user=actor_user)
    try:
        grant_supplier_welcome_pragmadinar(supplier, actor=actor_user)
    except Exception:
        logger.exception("Welcome Pragmadinar grant failed for supplier %s", supplier.pk)
    if actor_user:
        log_seller_event(
            actor_user,
            Event.EventType.SELLER_PROFILE_CREATED,
            {"supplier_id": supplier.id},
        )


def handle_seller_login(user: User) -> None:
    if not user.supplier_id:
        return

    today = timezone.localdate()
    with transaction.atomic():
        score = (
            SupplierScore.objects.select_for_update()
            .get_or_create(
                supplier_id=user.supplier_id,
                defaults={"score_total": BASE_SCORE},
            )[0]
        )

        if score.last_login_date == today:
            log_seller_event(
                user,
                Event.EventType.SELLER_LOGIN,
                {"supplier_id": user.supplier_id, "duplicate_today": True},
            )
            return

        if score.last_login_date:
            gap = (today - score.last_login_date).days
            if gap == 1:
                score.presence_streak += 1
                if score.presence_streak >= 2:
                    apply_score_delta(
                        user.supplier_id,
                        "presence",
                        10,
                        "presence_streak",
                        actor_user=user,
                        extra_context={"streak": score.presence_streak},
                    )
            elif gap > 1:
                missed_days = gap - 1
                apply_score_delta(
                    user.supplier_id,
                    "presence",
                    -5 * missed_days,
                    "presence_absence",
                    actor_user=user,
                    extra_context={"missed_days": missed_days},
                )
                score.presence_streak = 1
        else:
            score.presence_streak = 1

        score.last_login_date = today
        score.save(update_fields=["last_login_date", "presence_streak", "updated_at"])

    log_seller_event(
        user,
        Event.EventType.SELLER_LOGIN,
        {"supplier_id": user.supplier_id},
    )


def apply_daily_presence_penalties(for_day: date | None = None) -> int:
    """Penalize suppliers who did not log in on the given day (-5 per missed day)."""
    target_day = for_day or (timezone.localdate() - timedelta(days=1))
    penalized = 0

    for score in SupplierScore.objects.select_related("supplier").filter(
        supplier__is_active=True,
        supplier__is_ghost=False,
    ):
        last_login = score.last_login_date
        if last_login is None or last_login >= target_day:
            continue
        missed = (target_day - last_login).days
        if missed <= 0:
            continue
        apply_score_delta(
            score.supplier_id,
            "presence",
            -5 * missed,
            "presence_absence_daily",
            extra_context={"missed_days": missed, "through_day": target_day.isoformat()},
        )
        penalized += 1

    return penalized


def handle_product_added(supplier_id: int, product_id: int, *, actor_user: User) -> None:
    from user.models import Product
    from user.product_completeness import (
        PRODUCT_ADD_BASE_POINTS,
        calculate_product_completeness,
        catalog_points_for_product_add,
    )

    product = (
        Product.objects.filter(pk=product_id, supplier_id=supplier_id)
        .select_related("category", "subcategory")
        .prefetch_related("images", "search_tags", "attribute_values__attribute")
        .first()
    )
    completeness_pct = calculate_product_completeness(product) if product else 0
    points = catalog_points_for_product_add(completeness_pct)

    apply_score_delta(
        supplier_id,
        "catalog",
        points,
        "product_added",
        actor_user=actor_user,
        extra_context={
            "product_id": product_id,
            "completeness_pct": completeness_pct,
            "base_points": PRODUCT_ADD_BASE_POINTS,
            "catalog_points": points,
        },
    )


def handle_product_edited(supplier_id: int, product_id: int, *, actor_user: User) -> None:
    apply_score_delta(
        supplier_id,
        "catalog",
        5,
        "product_updated",
        actor_user=actor_user,
        extra_context={"product_id": product_id},
    )


def handle_product_deleted(supplier_id: int, product_id: int, *, actor_user: User) -> None:
    apply_score_delta(
        supplier_id,
        "catalog",
        -5,
        "product_deleted",
        actor_user=actor_user,
        extra_context={"product_id": product_id},
    )


def handle_profile_updated(
    supplier: Supplier,
    *,
    actor_user: User,
    previous: dict[str, Any] | None = None,
) -> None:
    award_profile_bonuses_for_supplier(supplier, actor_user=actor_user)
    log_seller_event(
        actor_user,
        Event.EventType.SELLER_PROFILE_UPDATED,
        {
            "supplier_id": supplier.id,
            "previous": previous or {},
        },
    )


def handle_associated_user_added(supplier_id: int, user_id: int) -> None:
    score = ensure_supplier_score(supplier_id)
    awarded = score.profile_bonuses_awarded.get("associated_users") or []
    if user_id in awarded:
        return
    if User.objects.filter(supplier_id=supplier_id).count() <= 1:
        return

    apply_score_delta(
        supplier_id,
        "profile",
        25,
        "profile_associated_user",
        extra_context={"user_id": user_id},
    )
    score.refresh_from_db()
    bonuses = dict(score.profile_bonuses_awarded or {})
    user_ids = list(bonuses.get("associated_users") or [])
    user_ids.append(user_id)
    bonuses["associated_users"] = user_ids
    score.profile_bonuses_awarded = bonuses
    score.save(update_fields=["profile_bonuses_awarded", "updated_at"])


def handle_order_created(supplier_id: int, order_id: int, *, buyer: User | None) -> None:
    apply_score_delta(
        supplier_id,
        "commercial",
        100,
        "commercial_order",
        extra_context={"order_id": order_id},
    )
    if buyer:
        log_buyer_event(
            buyer,
            Event.EventType.BUYER_ORDER_CREATED,
            {"supplier_id": supplier_id, "order_id": order_id},
        )


def handle_rfq_created(supplier_id: int, rfq_id: int, *, buyer: User | None) -> None:
    apply_score_delta(
        supplier_id,
        "commercial",
        60,
        "commercial_rfq",
        extra_context={"rfq_id": rfq_id},
    )
    if buyer:
        log_buyer_event(
            buyer,
            Event.EventType.BUYER_QUOTATION_REQUEST,
            {"supplier_id": supplier_id, "rfq_id": rfq_id},
        )
    log_system_event(
        Event.EventType.SELLER_QUOTATION_RECEIVED,
        {"supplier_id": supplier_id, "rfq_id": rfq_id},
    )


def handle_supplier_favorited(
    supplier_id: int,
    *,
    buyer: User,
    source: Literal["supplier", "product"] = "supplier",
    product_id: int | None = None,
) -> None:
    apply_score_delta(
        supplier_id,
        "commercial",
        50,
        "commercial_favorite",
        extra_context={"source": source, "product_id": product_id},
    )
    event_type = (
        Event.EventType.BUYER_PRODUCT_FAVORITE
        if source == "product"
        else Event.EventType.BUYER_FOLLOW_SUPPLIER
    )
    log_buyer_event(
        buyer,
        event_type,
        {
            "supplier_id": supplier_id,
            **({"product_id": product_id} if product_id else {}),
        },
    )


def handle_supplier_message_reply(
    supplier_id: int,
    buyer_id: int,
    reply: Message,
    *,
    actor_user: User,
) -> None:
    last_buyer_message = (
        Message.objects.filter(
            supplier_id=supplier_id,
            buyer_id=buyer_id,
            sender_id=buyer_id,
            created_at__lt=reply.created_at,
        )
        .order_by("-created_at")
        .first()
    )
    if not last_buyer_message:
        log_seller_event(
            actor_user,
            Event.EventType.SELLER_MESSAGE_REPLY,
            {"supplier_id": supplier_id, "buyer_id": buyer_id, "message_id": reply.id},
        )
        return

    reply_day = timezone.localtime(reply.created_at).date()
    buyer_day = timezone.localtime(last_buyer_message.created_at).date()
    if reply_day == buyer_day:
        apply_score_delta(
            supplier_id,
            "responsivity",
            20,
            "message_reply_same_day",
            actor_user=actor_user,
            extra_context={
                "buyer_id": buyer_id,
                "message_id": reply.id,
                "in_reply_to": last_buyer_message.id,
            },
        )
    else:
        log_seller_event(
            actor_user,
            Event.EventType.SELLER_MESSAGE_REPLY,
            {
                "supplier_id": supplier_id,
                "buyer_id": buyer_id,
                "message_id": reply.id,
                "same_day": False,
            },
        )


def backfill_supplier_score_from_history(supplier_id: int) -> SupplierScore:
    """Rebuild score from historical activity (for migration / init command)."""
    from user.models import Order, Product, RFQRequest, BuyerFavoriteSupplier

    supplier = Supplier.objects.get(pk=supplier_id)
    score, _ = SupplierScore.objects.update_or_create(
        supplier_id=supplier_id,
        defaults={
            "score_presence": 0,
            "score_responsivity": 0,
            "score_catalog": 0,
            "score_profile": 0,
            "score_commercial": 0,
            "score_total": BASE_SCORE,
            "profile_bonuses_awarded": {},
        },
    )

    catalog = Product.objects.filter(supplier_id=supplier_id).count() * 25
    catalog -= Product.objects.filter(supplier_id=supplier_id, is_active=False).count() * 5
    commercial = Order.objects.filter(supplier_id=supplier_id).count() * 100
    commercial += RFQRequest.objects.filter(supplier_id=supplier_id).count() * 60
    commercial += BuyerFavoriteSupplier.objects.filter(supplier_id=supplier_id).count() * 50

    profile = 0
    bonuses: dict[str, Any] = {}
    if supplier.email:
        profile += 10
        bonuses[_profile_bonus_key("email")] = True
    if supplier.website:
        profile += 10
        bonuses[_profile_bonus_key("website")] = True
    if supplier.whatsapp_number:
        profile += 15
        bonuses[_profile_bonus_key("phone")] = True
    if _supplier_has_location(supplier):
        profile += 15
        bonuses[_profile_bonus_key("location")] = True
    extra_users = list(
        User.objects.filter(supplier_id=supplier_id)
        .order_by("id")
        .values_list("id", flat=True)[1:]
    )
    profile += len(extra_users) * 25
    if extra_users:
        bonuses["associated_users"] = extra_users

    score.score_catalog = catalog
    score.score_commercial = commercial
    score.score_profile = profile
    score.profile_bonuses_awarded = bonuses
    _refresh_total(score)
    score.save()
    _emit_score_updated(score, "backfill", score.score_total - BASE_SCORE)
    return score
