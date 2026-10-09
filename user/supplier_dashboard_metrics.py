"""Supplier dashboard scorecards.

Subcategory filter
------------------
« Toutes » counts the supplier's own subcategories (active products and declared
interests). A chosen subcategory keeps only events that belong to it.

Events that cannot be attached to one subcategory are excluded from that filter
and stay in « Toutes »:

* boutique search appearances (source ``suppliers`` or no source);
* product-search appearances whose search has no category, or a category that is
  not this subcategory's name;
* direct boutique visits (profile views that are not a product-page side effect);
* sponsored banner views (ledger rows are not tied to a subcategory);
* quote, sample and order rows without a product;
* new conversations (buyer ↔ supplier, not a subcategory).

Product views, product favorites and product-linked orders / quotes / samples
follow ``product.subcategory``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Min, Q, Sum
from django.db.models.functions import Abs
from django.utils import timezone

from user.analytics import (
    MONTH_SHORT,
    _supplier_sponsored_views,
)
from user.models import (
    BuyerFavoriteProduct,
    BuyerFavoriteSupplier,
    Category,
    Event,
    Message,
    Order,
    PragmadinarLedger,
    Product,
    RFQRequest,
    SampleRequest,
    Supplier,
)
from user.platform_config import get_int
from user.pragmadinar import wallet_payload
from user.reporting_period import ReportPeriod, filter_created_in_local_period, trunc_local_day
from user.scoring import ensure_supplier_score, get_supplier_rank
from user.supplier_competitors import active_subcategory_ids, count_direct_competitors

ANONYMITY_HINT = "Données de marché insuffisantes pour préserver l'anonymat"
INTERACTIONS_HINT = (
    "Commandes, demandes de devis, demandes d'échantillons, conversations, ajouts aux favoris"
)

LABELS = {
    "search_appearances": "Apparition aux résultats de recherches",
    "appearance_rate": "Pourcentage d'apparition",
    "all_visits": "Toutes les visites",
    "visit_share": "Proportion de visites par rapport au marché",
    "interactions": "Interactions confondues",
    "sponsored_banner_views": "Vues sur bannière sponsorisée",
    "store_appearances": "Apparition de boutiques aux résultats de recherches",
    "store_appearance_rate": "Pourcentage d'apparition de boutique par rapport au marché",
    "store_visits": "Visite de boutique",
    "store_visit_share": "Proportion de visites boutiques par rapport au marché",
    "product_visits": "Visite de page produit",
    "product_visit_share": "Proportion de visites de pages produit par rapport au marché",
    "product_search_appearances": "Apparition de produits aux résultats de recherches",
    "product_appearance_share": "Pourcentage d'apparition de produits par rapport au marché",
    "score": "Score Paknomik",
    "rank": "Classement",
    "categories": "Catégorie",
    "products": "Produit",
    "competitors": "Concurrents directs",
    "pragmadinar": "Pragmadinar",
    "orders": "Commande",
    "quotes": "Demande de devis",
    "samples": "Demande d'échantillon",
    "conversations": "Conversation",
    "followers": "Followers",
    "product_favorites": "Ajout aux favoris (produits)",
}


def _min_suppliers() -> int:
    return max(1, get_int("dashboard_market_min_suppliers", 3))


def _local_day(value) -> date:
    return timezone.localtime(value).date()


def _days(start: date, end: date) -> list[date]:
    span = (end - start).days
    if span <= 45:
        return [start + timedelta(days=offset) for offset in range(span + 1)]
    step = span / 30
    offsets = sorted({max(0, min(span, int(round(index * step)))) for index in range(31)})
    return [start + timedelta(days=offset) for offset in offsets]


def _variation(current: float, previous: float) -> int | None:
    """Percent change. None when the previous period is 0 (shown as —)."""
    if previous == 0:
        return None
    return int(round((current - previous) / previous * 100))


def _change_state(change: int | None, *, masked: bool = False) -> str:
    if masked:
        return "masked"
    if change is None:
        return "unavailable"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _metric(
    key: str,
    value: str,
    change: int | None = None,
    *,
    hint: str | None = None,
    masked: bool = False,
    action: str | None = None,
) -> dict:
    payload = {
        "key": key,
        "label": LABELS[key],
        "value": "—" if masked else value,
        "change": None if masked else change,
        "changeState": _change_state(change, masked=masked),
    }
    if hint or masked:
        payload["hint"] = ANONYMITY_HINT if masked else hint
    if action and not masked:
        payload["action"] = action
    return payload


def _pct_label(rate: float) -> str:
    return f"{rate:.1f}%"


def _share(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole * 100, 1)


def _compact_amount(value) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    if amount == amount.to_integral():
        return str(int(amount))
    return f"{amount:.2f}"


def _subcategory_options(supplier: Supplier) -> list[Category]:
    ids = set(active_subcategory_ids(supplier))
    ids.update(supplier.interested_subcategories.values_list("id", flat=True))
    if not ids:
        return []
    return list(
        Category.objects.filter(pk__in=ids, parent__isnull=False).order_by("name_fr", "name_en")
    )


def _market_supplier_ids(supplier: Supplier, subcategory_id: int | None) -> set[int]:
    if subcategory_id:
        found = set(
            Supplier.objects.filter(
                is_active=True,
                products__is_active=True,
                products__subcategory_id=subcategory_id,
            ).values_list("id", flat=True)
        )
    else:
        subcategory_ids = set(active_subcategory_ids(supplier))
        subcategory_ids.update(supplier.interested_subcategories.values_list("id", flat=True))
        if not subcategory_ids:
            found = set()
        else:
            found = set(
                Supplier.objects.filter(
                    is_active=True,
                    products__is_active=True,
                    products__subcategory_id__in=subcategory_ids,
                ).values_list("id", flat=True)
            )
    found.add(supplier.id)
    return found


def _json_in(field: str, values) -> Q:
    """Equality OR, because JSON ``__in`` misses integer keys on SQLite."""
    collected = list(values)
    if not collected:
        return Q(pk__in=[])
    query = Q()
    for value in collected:
        query |= Q(**{field: value})
    return query


def _event_qs(event_type: str, supplier_ids: set[int], start: date, end: date):
    qs = Event.objects.filter(event_type=event_type).filter(
        _json_in("context__supplier_id", supplier_ids)
    )
    return filter_created_in_local_period(qs, start, end)


def _counts(qs) -> dict[int, int]:
    """Count by supplier id from the JSON context.

    Grouping on ``context__supplier_id`` returns null on SQLite, so the rows
    are tallied in Python. Only the supplier id is read — never another
    supplier's payload.
    """
    result: dict[int, int] = {}
    for context in qs.values_list("context", flat=True):
        if not isinstance(context, dict):
            continue
        supplier_id = context.get("supplier_id")
        if supplier_id is None:
            continue
        try:
            key = int(supplier_id)
        except (TypeError, ValueError):
            continue
        result[key] = result.get(key, 0) + 1
    return result


def _search_ids_for_subcategory(subcategory: Category, start: date, end: date) -> set[str]:
    names = {
        (subcategory.name_fr or "").strip().lower(),
        (subcategory.name_en or "").strip().lower(),
    }
    names.discard("")
    if not names:
        return set()
    qs = filter_created_in_local_period(
        Event.objects.filter(event_type=Event.EventType.BUYER_SEARCH),
        start,
        end,
    )
    matched: set[str] = set()
    for context in qs.values_list("context", flat=True):
        if not isinstance(context, dict):
            continue
        category_name = str(context.get("category") or "").strip().lower()
        search_id = context.get("search_id")
        if search_id and category_name in names:
            matched.add(str(search_id))
    return matched


def _appearance_qs(supplier_ids, start, end, *, source: str | None, search_ids: set[str] | None):
    qs = _event_qs(Event.EventType.SYSTEM_APPEAR_IN_SEARCH, supplier_ids, start, end)
    if source == "products":
        qs = qs.filter(context__source="products")
    elif source == "suppliers":
        # A missing source is a boutique appearance. exclude() drops those rows
        # because SQL treats NULL as not different from "products".
        qs = qs.filter(Q(context__source__isnull=True) | ~Q(context__source="products"))
    if search_ids is not None:
        if not search_ids:
            return qs.none()
        qs = qs.filter(_json_in("context__search_id", search_ids))
    return qs


def _store_visit_qs(supplier_ids, start, end):
    qs = _event_qs(Event.EventType.BUYER_SUPPLIER_VIEW, supplier_ids, start, end)
    return qs.filter(Q(context__source__isnull=True) | ~Q(context__source="product_page"))


def _product_visit_qs(supplier_ids, start, end, subcategory_id: int | None):
    qs = _event_qs(Event.EventType.BUYER_PRODUCT_VIEW, supplier_ids, start, end)
    if subcategory_id:
        product_ids = list(
            Product.objects.filter(subcategory_id=subcategory_id, is_active=True).values_list(
                "id", flat=True
            )
        )
        if not product_ids:
            return qs.none()
        qs = qs.filter(_json_in("context__product_id", product_ids))
    return qs


def _pair(supplier_id: int, counts: dict[int, int]) -> tuple[int, int]:
    own = counts.get(supplier_id, 0)
    return own, sum(counts.values())


def _daily(qs) -> dict[date, int]:
    from django.db.models import Count

    rows = qs.annotate(day=trunc_local_day("created_at")).values("day").annotate(total=Count("id"))
    return {row["day"]: int(row["total"]) for row in rows if row["day"]}


def _running(daily: dict[date, int], days: list[date]) -> tuple[list[int], list[int]]:
    detailed = [daily.get(day, 0) for day in days]
    cumulative = []
    total = 0
    for value in detailed:
        total += value
        cumulative.append(total)
    return detailed, cumulative


def _created_in_period(qs, start: date, end: date):
    return filter_created_in_local_period(qs, start, end)


def _order_qs(supplier_id: int, start: date, end: date, subcategory_id: int | None):
    qs = _created_in_period(Order.objects.filter(supplier_id=supplier_id), start, end)
    if subcategory_id:
        qs = qs.filter(product__subcategory_id=subcategory_id)
    return qs


def _quote_qs(supplier_id: int, start: date, end: date, subcategory_id: int | None):
    qs = _created_in_period(RFQRequest.objects.filter(supplier_id=supplier_id), start, end)
    if subcategory_id:
        qs = qs.filter(product__subcategory_id=subcategory_id)
    return qs


def _sample_qs(supplier_id: int, start: date, end: date, subcategory_id: int | None):
    qs = _created_in_period(SampleRequest.objects.filter(supplier_id=supplier_id), start, end)
    if subcategory_id:
        qs = qs.filter(product__subcategory_id=subcategory_id)
    return qs


def _favorite_qs(supplier_id: int, start: date, end: date, subcategory_id: int | None):
    qs = _created_in_period(
        BuyerFavoriteProduct.objects.filter(product__supplier_id=supplier_id),
        start,
        end,
    )
    if subcategory_id:
        qs = qs.filter(product__subcategory_id=subcategory_id)
    return qs


def _new_conversations(supplier_id: int, start: date, end: date) -> int:
    return sum(_conversation_days(supplier_id, start, end).values())


def _conversation_days(supplier_id: int, start: date, end: date) -> dict[date, int]:
    started = (
        Message.objects.filter(supplier_id=supplier_id)
        .values("buyer_id")
        .annotate(started=Min("created_at"))
    )
    daily: dict[date, int] = {}
    for row in started:
        if row["started"] is None:
            continue
        day = _local_day(row["started"])
        if start <= day <= end:
            daily[day] = daily.get(day, 0) + 1
    return daily


def _model_days(qs) -> dict[date, int]:
    from django.db.models import Count

    rows = qs.annotate(day=trunc_local_day("created_at")).values("day").annotate(total=Count("id"))
    return {row["day"]: int(row["total"]) for row in rows if row["day"]}


def _merge_days(*parts: dict[date, int]) -> dict[date, int]:
    merged: dict[date, int] = {}
    for part in parts:
        for day, total in part.items():
            merged[day] = merged.get(day, 0) + total
    return merged


def _score_series(supplier_id: int, days: list[date], current_score: int) -> tuple[list[int], list[int]]:
    if not days:
        return [], []
    end_dt_day = days[-1]
    events = (
        Event.objects.filter(
            event_type=Event.EventType.SYSTEM_SCORE_UPDATED,
            context__supplier_id=supplier_id,
        )
        .order_by("created_at")
        .values_list("created_at", "context")
    )
    history: list[tuple[date, int]] = []
    for created_at, context in events:
        if created_at is None or not isinstance(context, dict):
            continue
        try:
            score = int(context.get("score_total"))
        except (TypeError, ValueError):
            continue
        history.append((_local_day(created_at), score))

    opening = current_score
    prior = [score for day, score in history if day < days[0]]
    if prior:
        opening = prior[-1]
    elif not history:
        opening = current_score

    last_by_day: dict[date, int] = {}
    for day, score in history:
        if days[0] <= day <= end_dt_day:
            last_by_day[day] = score

    detailed: list[int] = []
    cumulative: list[int] = []
    running = opening
    for day in days:
        today_score = last_by_day.get(day, running)
        detailed.append(today_score - running)
        running = today_score
        cumulative.append(running - opening)
    return detailed, cumulative


def _pragmadinar_days(supplier_id: int, start: date, end: date) -> dict[date, int]:
    qs = filter_created_in_local_period(
        PragmadinarLedger.objects.filter(
            wallet__supplier_id=supplier_id,
            entry_type=PragmadinarLedger.EntryType.SPEND,
            status=PragmadinarLedger.Status.POSTED,
        ),
        start,
        end,
    )
    rows = (
        qs.annotate(day=trunc_local_day("created_at"))
        .values("day")
        .annotate(spent=Sum(Abs("amount")))
    )
    daily: dict[date, int] = {}
    for row in rows:
        if not row["day"]:
            continue
        spent = row["spent"] or 0
        daily[row["day"]] = float(Decimal(spent))
    return daily


def _series(name: str, detailed: list[int], cumulative: list[int]) -> dict:
    return {"name": name, "detailed": detailed, "cumulative": cumulative}


def _visibility_block(
    supplier_id: int,
    market_ids: set[int],
    start: date,
    end: date,
    subcategory: Category | None,
) -> dict:
    search_ids = None
    if subcategory is not None:
        search_ids = _search_ids_for_subcategory(subcategory, start, end)

    if subcategory is None:
        appearances = _appearance_qs(market_ids, start, end, source=None, search_ids=None)
        store_appearances = _appearance_qs(market_ids, start, end, source="suppliers", search_ids=None)
        store_visits = _store_visit_qs(market_ids, start, end)
    else:
        appearances = _appearance_qs(
            market_ids, start, end, source="products", search_ids=search_ids
        )
        store_appearances = _appearance_qs(market_ids, start, end, source="suppliers", search_ids=set())
        store_visits = _store_visit_qs(market_ids, start, end).none()

    product_visits = _product_visit_qs(market_ids, start, end, subcategory.pk if subcategory else None)

    return {
        "appearances": _counts(appearances),
        "store_appearances": _counts(store_appearances),
        "store_visits": _counts(store_visits),
        "product_visits": _counts(product_visits),
        "appearance_daily": _daily(appearances.filter(context__supplier_id=supplier_id)),
        "store_visit_daily": _daily(store_visits.filter(context__supplier_id=supplier_id)),
        "product_visit_daily": _daily(product_visits.filter(context__supplier_id=supplier_id)),
        "appearance_group_daily": _daily(appearances),
        "store_visit_group_daily": _daily(store_visits),
        "product_visit_group_daily": _daily(product_visits),
    }


def _rate_metric(key: str, current_part: int, current_whole: int, previous_part: int, previous_whole: int, *, masked: bool):
    current_rate = _share(current_part, current_whole)
    previous_rate = _share(previous_part, previous_whole)
    return _metric(
        key,
        _pct_label(current_rate),
        _variation(current_rate, previous_rate),
        masked=masked,
    )


def build_supplier_dashboard_sections(
    supplier: Supplier,
    period: ReportPeriod,
    subcategory_id: int | None = None,
) -> dict:
    options = _subcategory_options(supplier)
    selected = next((item for item in options if item.pk == subcategory_id), None)
    market_ids = _market_supplier_ids(supplier, selected.pk if selected else None)
    minimum = _min_suppliers()
    masked = len(market_ids) < minimum
    supplier_id = supplier.id

    current = _visibility_block(supplier_id, market_ids, period.start, period.end, selected)
    previous = _visibility_block(
        supplier_id, market_ids, period.prev_start, period.prev_end, selected
    )

    appear_now, appear_market = _pair(supplier_id, current["appearances"])
    appear_prev, appear_market_prev = _pair(supplier_id, previous["appearances"])
    store_now, store_market = _pair(supplier_id, current["store_appearances"])
    store_prev, store_market_prev = _pair(supplier_id, previous["store_appearances"])
    # Product appearances are the remainder of the two queries already run
    # (all appearances, boutique appearances). Card 1 = card 7 + card 13.
    product_appear_now = appear_now - store_now
    product_appear_prev = appear_prev - store_prev
    product_appear_market = appear_market - store_market
    product_appear_market_prev = appear_market_prev - store_market_prev
    store_visits_now, store_visits_market = _pair(supplier_id, current["store_visits"])
    store_visits_prev, store_visits_market_prev = _pair(supplier_id, previous["store_visits"])
    product_now, product_market = _pair(supplier_id, current["product_visits"])
    product_prev, product_market_prev = _pair(supplier_id, previous["product_visits"])

    visits_now = store_visits_now + product_now
    visits_prev = store_visits_prev + product_prev
    visits_market = store_visits_market + product_market
    visits_market_prev = store_visits_market_prev + product_market_prev

    orders_now = _order_qs(supplier_id, period.start, period.end, selected.pk if selected else None).count()
    orders_prev = _order_qs(
        supplier_id, period.prev_start, period.prev_end, selected.pk if selected else None
    ).count()
    quotes_now = _quote_qs(supplier_id, period.start, period.end, selected.pk if selected else None).count()
    quotes_prev = _quote_qs(
        supplier_id, period.prev_start, period.prev_end, selected.pk if selected else None
    ).count()
    samples_now = _sample_qs(supplier_id, period.start, period.end, selected.pk if selected else None).count()
    samples_prev = _sample_qs(
        supplier_id, period.prev_start, period.prev_end, selected.pk if selected else None
    ).count()
    favorites_now = _favorite_qs(
        supplier_id, period.start, period.end, selected.pk if selected else None
    ).count()
    favorites_prev = _favorite_qs(
        supplier_id, period.prev_start, period.prev_end, selected.pk if selected else None
    ).count()
    if selected is None:
        conversations_now = _new_conversations(supplier_id, period.start, period.end)
        conversations_prev = _new_conversations(supplier_id, period.prev_start, period.prev_end)
    else:
        conversations_now = 0
        conversations_prev = 0

    interactions_now = orders_now + quotes_now + samples_now + conversations_now + favorites_now
    interactions_prev = orders_prev + quotes_prev + samples_prev + conversations_prev + favorites_prev

    if selected is None:
        sponsored_now = _supplier_sponsored_views(supplier_id, period.start, period.end)
        sponsored_prev = _supplier_sponsored_views(supplier_id, period.prev_start, period.prev_end)
    else:
        sponsored_now = 0
        sponsored_prev = 0

    visibility = [
        _metric("search_appearances", str(appear_now), _variation(appear_now, appear_prev)),
        _rate_metric(
            "appearance_rate",
            appear_now,
            appear_market,
            appear_prev,
            appear_market_prev,
            masked=masked,
        ),
        _metric("all_visits", str(visits_now), _variation(visits_now, visits_prev)),
        _rate_metric(
            "visit_share",
            visits_now,
            visits_market,
            visits_prev,
            visits_market_prev,
            masked=masked,
        ),
        _metric(
            "interactions",
            str(interactions_now),
            _variation(interactions_now, interactions_prev),
            hint=INTERACTIONS_HINT,
        ),
        _metric(
            "sponsored_banner_views",
            str(sponsored_now),
            _variation(sponsored_now, sponsored_prev),
            action="sponsor",
        ),
        _metric("store_appearances", str(store_now), _variation(store_now, store_prev)),
        _rate_metric(
            "store_appearance_rate",
            store_now,
            store_market,
            store_prev,
            store_market_prev,
            masked=masked,
        ),
        _metric("store_visits", str(store_visits_now), _variation(store_visits_now, store_visits_prev)),
        _rate_metric(
            "store_visit_share",
            store_visits_now,
            store_visits_market,
            store_visits_prev,
            store_visits_market_prev,
            masked=masked,
        ),
        _metric("product_visits", str(product_now), _variation(product_now, product_prev)),
        _rate_metric(
            "product_visit_share",
            product_now,
            product_market,
            product_prev,
            product_market_prev,
            masked=masked,
        ),
        _metric(
            "product_search_appearances",
            str(product_appear_now),
            _variation(product_appear_now, product_appear_prev),
        ),
        _rate_metric(
            "product_appearance_share",
            product_appear_now,
            product_appear_market,
            product_appear_prev,
            product_appear_market_prev,
            masked=masked,
        ),
    ]

    score_record = ensure_supplier_score(supplier_id)
    rank, total_suppliers = get_supplier_rank(supplier_id)
    category_count = supplier.interested_categories.count()
    if category_count == 0:
        category_count = (
            supplier.products.filter(is_active=True, category_id__isnull=False)
            .values("category_id")
            .distinct()
            .count()
        )
    product_count = supplier.products.filter(is_active=True).count()
    competitors = count_direct_competitors(supplier, cap=20)
    balance = wallet_payload(supplier)["balance"]
    followers = BuyerFavoriteSupplier.objects.filter(supplier_id=supplier_id).count()

    activity = [
        {"key": "score", "label": LABELS["score"], "value": str(score_record.score_total)},
        {"key": "rank", "label": LABELS["rank"], "value": f"{rank}/{total_suppliers}"},
        {"key": "categories", "label": LABELS["categories"], "value": str(category_count)},
        {"key": "products", "label": LABELS["products"], "value": str(product_count)},
        {"key": "competitors", "label": LABELS["competitors"], "value": str(competitors)},
        {"key": "pragmadinar", "label": LABELS["pragmadinar"], "value": _compact_amount(balance)},
        {"key": "orders", "label": LABELS["orders"], "value": str(orders_now)},
        {"key": "quotes", "label": LABELS["quotes"], "value": str(quotes_now)},
        {"key": "samples", "label": LABELS["samples"], "value": str(samples_now)},
        {"key": "conversations", "label": LABELS["conversations"], "value": str(conversations_now)},
        {"key": "followers", "label": LABELS["followers"], "value": str(followers)},
        {"key": "product_favorites", "label": LABELS["product_favorites"], "value": str(favorites_now)},
    ]

    days = _days(period.start, period.end)
    labels = [f"{day.day} {MONTH_SHORT[day.month - 1]}" for day in days]
    appear_detailed, appear_cumulative = _running(current["appearance_daily"], days)
    visit_daily = _merge_days(current["store_visit_daily"], current["product_visit_daily"])
    visit_detailed, visit_cumulative = _running(visit_daily, days)
    interaction_daily = _merge_days(
        _model_days(_order_qs(supplier_id, period.start, period.end, selected.pk if selected else None)),
        _model_days(_quote_qs(supplier_id, period.start, period.end, selected.pk if selected else None)),
        _model_days(_sample_qs(supplier_id, period.start, period.end, selected.pk if selected else None)),
        _model_days(_favorite_qs(supplier_id, period.start, period.end, selected.pk if selected else None)),
        _conversation_days(supplier_id, period.start, period.end) if selected is None else {},
    )
    interaction_detailed, interaction_cumulative = _running(interaction_daily, days)
    score_detailed, score_cumulative = _score_series(supplier_id, days, score_record.score_total)
    pragma_detailed, pragma_cumulative = _running(
        _pragmadinar_days(supplier_id, period.start, period.end),
        days,
    )

    chart = {
        "labels": labels,
        "series": [
            _series("Apparition", appear_detailed, appear_cumulative),
            _series("Visite", visit_detailed, visit_cumulative),
            _series("Interaction", interaction_detailed, interaction_cumulative),
            _series("Variation de score", score_detailed, score_cumulative),
            _series("Pragmadinar", pragma_detailed, pragma_cumulative),
        ],
        "totals": {
            "Apparition": appear_now,
            "Visite": visits_now,
            "Visite de profil": visits_now,
            "Interaction": interactions_now,
            "Variation de score": score_cumulative[-1] if score_cumulative else 0,
            "Pragmadinar": pragma_cumulative[-1] if pragma_cumulative else 0,
        },
    }

    return {
        "subcategories": [
            {"id": item.pk, "name": item.name_fr or item.name_en} for item in options
        ],
        "subcategoryId": selected.pk if selected else None,
        "market": {
            "supplierCount": len(market_ids),
            "minSuppliers": minimum,
            "masked": masked,
        },
        "visibility": visibility,
        "activity": activity,
        "chart": chart,
    }
