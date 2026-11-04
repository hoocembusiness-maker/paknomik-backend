"""Anonymized category benchmark for the supplier space.

The comparison group is the same set as the dashboard subcategory filter.
Unattributable events follow the dashboard rule: they stay in « Toutes » and
drop out of a chosen subcategory.

Percentage cards use the whole marketplace as the denominator. That is
deliberately wider than the dashboard, whose denominator is the comparison
group.

Only aggregates leave this module. Per-supplier maps stay in memory and are
never returned, cached, or logged.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Min, Q, Sum
from django.db.models.functions import Abs
from django.utils import timezone

from user.analytics import MONTH_SHORT, _supplier_sponsored_views
from user.models import (
    BuyerFavoriteProduct,
    BuyerFavoriteSupplier,
    Event,
    Message,
    Order,
    PragmadinarLedger,
    PragmadinarWallet,
    Product,
    RFQRequest,
    SampleRequest,
    SupplierScore,
)
from user.platform_config import get_bool, get_int
from user.pragmadinar import wallet_payload
from user.response_time import mean_minutes, response_delays
from user.reporting_period import ReportPeriod, filter_created_in_local_period, trunc_local_day
from user.scoring import BASE_SCORE, ensure_supplier_score
from user.supplier_competitors import count_direct_competitors
from user.supplier_dashboard_metrics import (
    LABELS,
    _conversation_days,
    _days,
    _favorite_qs,
    _market_supplier_ids,
    _merge_days,
    _model_days,
    _new_conversations,
    _order_qs,
    _pair,
    _pragmadinar_days,
    _quote_qs,
    _running,
    _sample_qs,
    _score_series,
    _subcategory_options,
    _visibility_block,
)

ANONYMITY_HINT = "Données de catégorie insuffisantes pour préserver l'anonymat"
CATEGORY_AVERAGE = "Moyenne de la Catégorie"

BENCHMARK_LABELS = {
    **LABELS,
    "appearance_rate": "Pourcentage d'apparition de la catégorie",
    "category_rank": "Classement Catégorie",
    "availability": "Disponibilité",
    "response_time": "Délai moyen de réponse",
    "products": "Produits",
}


def _min_suppliers() -> int:
    return max(1, get_int("benchmark_min_suppliers", 5))


def _show_pragmadinar() -> bool:
    return get_bool("benchmark_category_show_pragmadinar", True)


def _share_int(part: float, whole: float) -> int | None:
    if whole <= 0:
        return None
    share = (Decimal(str(part)) * Decimal(100) / Decimal(str(whole))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(share)


def _pct(part: float, whole: float) -> str | None:
    if whole <= 0:
        return None
    rounded = round(part / whole * 100, 1)
    if rounded == int(rounded):
        return f"{int(rounded)}%"
    return f"{rounded:.1f}%"


def _amount(value) -> str:
    amount = Decimal(str(value or 0))
    if amount == amount.to_integral():
        return str(int(amount))
    return f"{amount:.2f}"


def _volume_card(key: str, category: float, supplier: float, *, masked: bool, action: str | None = None) -> dict:
    payload = {
        "key": key,
        "label": BENCHMARK_LABELS[key],
        "pillTone": "volume",
        "value": "—",
        "pill": "—",
    }
    if action:
        payload["action"] = action
    if masked:
        payload["hint"] = ANONYMITY_HINT
        return payload
    share = _share_int(supplier, category)
    payload["value"] = _amount(category)
    payload["pill"] = "—" if share is None else f"{_amount(supplier)} ({share}%)"
    return payload


def _percent_card(
    key: str,
    category_part: float,
    supplier_part: float,
    marketplace: float,
    *,
    masked: bool,
) -> dict:
    payload = {
        "key": key,
        "label": BENCHMARK_LABELS[key],
        "pillTone": "percent",
        "value": "—",
        "pill": "—",
    }
    if masked:
        payload["hint"] = ANONYMITY_HINT
        return payload
    category_share = _pct(category_part, marketplace)
    supplier_market = _pct(supplier_part, marketplace)
    supplier_in_category = _share_int(supplier_part, category_part)
    payload["value"] = category_share or "—"
    if supplier_market is None or supplier_in_category is None:
        payload["pill"] = "—"
    else:
        payload["pill"] = f"{supplier_market} ({supplier_in_category}%)"
    return payload


def _plain_card(key: str, value: str, *, masked: bool = False) -> dict:
    payload = {
        "key": key,
        "label": BENCHMARK_LABELS[key],
        "pillTone": "none",
        "value": "—" if masked else value,
        "pill": None,
    }
    if masked:
        payload["hint"] = ANONYMITY_HINT
    return payload


def _marketplace_count(event_type: str, start: date, end: date, *, source: str | None = None) -> int:
    qs = filter_created_in_local_period(Event.objects.filter(event_type=event_type), start, end)
    if source == "products":
        qs = qs.filter(context__source="products")
    elif source == "suppliers":
        qs = qs.filter(Q(context__source__isnull=True) | ~Q(context__source="products"))
    elif source == "store_visits":
        qs = qs.filter(Q(context__source__isnull=True) | ~Q(context__source="product_page"))
    return qs.count()


def _sponsored_views(supplier_ids: set[int] | None, start: date, end: date) -> int:
    qs = PragmadinarLedger.objects.filter(
        entry_type=PragmadinarLedger.EntryType.SPEND,
        status=PragmadinarLedger.Status.POSTED,
    ).filter(
        Q(campaign__isnull=False)
        | Q(service__service_class="promotion", service__unit="view")
    )
    if supplier_ids is not None:
        if not supplier_ids:
            return 0
        qs = qs.filter(wallet__supplier_id__in=list(supplier_ids))
    total = filter_created_in_local_period(qs, start, end).aggregate(
        views=Sum("service__unit_quantity")
    )["views"]
    return int(total or 0)


def _group_model_count(model, supplier_ids: set[int], start: date, end: date, subcategory_id: int | None):
    qs = filter_created_in_local_period(model.objects.filter(supplier_id__in=list(supplier_ids)), start, end)
    if subcategory_id:
        qs = qs.filter(product__subcategory_id=subcategory_id)
    return qs.count(), _model_days(qs)


def _group_favorite_count(supplier_ids: set[int], start: date, end: date, subcategory_id: int | None):
    qs = filter_created_in_local_period(
        BuyerFavoriteProduct.objects.filter(product__supplier_id__in=list(supplier_ids)),
        start,
        end,
    )
    if subcategory_id:
        qs = qs.filter(product__subcategory_id=subcategory_id)
    return qs.count(), _model_days(qs)


def _group_conversation_days(supplier_ids: set[int], start: date, end: date) -> dict[date, int]:
    if not supplier_ids:
        return {}
    started = (
        Message.objects.filter(supplier_id__in=list(supplier_ids))
        .values("buyer_id", "supplier_id")
        .annotate(started=Min("created_at"))
    )
    daily: dict[date, int] = {}
    for row in started:
        if row["started"] is None:
            continue
        day = timezone.localtime(row["started"]).date()
        if start <= day <= end:
            daily[day] = daily.get(day, 0) + 1
    return daily


def _format_delay(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    total = int(Decimal(str(minutes)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if total < 0:
        total = 0
    if total < 60:
        return f"{total}min"
    hours, mins = divmod(total, 60)
    if mins:
        return f"{hours}h {mins}min"
    return f"{hours}h"


def _response_minutes(supplier_ids: set[int], focal_id: int, start: date, end: date) -> tuple[float | None, float | None]:
    """Mean reply delay of the focal supplier, and of every reply in the group.

    Replies cover messages, quote requests, quote demands and sample requests.
    """
    if not supplier_ids:
        return None, None
    delays = response_delays(supplier_ids, start=start, end=end)
    group_delays = [minutes for values in delays.values() for minutes in values]
    return mean_minutes(delays.get(focal_id, [])), mean_minutes(group_delays)


def _category_rank(supplier_id: int, group_ids: set[int], current_score: int) -> int:
    scores = {
        row.supplier_id: row.score_total
        for row in SupplierScore.objects.filter(supplier_id__in=list(group_ids))
    }
    better = 0
    for other_id in group_ids:
        if other_id == supplier_id:
            continue
        other_score = scores.get(other_id, BASE_SCORE)
        if other_score > current_score or (other_score == current_score and other_id < supplier_id):
            better += 1
    return better + 1


def _mean_series(rows: list[list]) -> list[float]:
    if not rows:
        return []
    width = len(rows[0])
    count = len(rows)
    return [sum(row[index] for row in rows) / count for index in range(width)]


def _average_running(daily: dict, days: list[date], suppliers: int) -> tuple[list[float], list[float]]:
    if suppliers <= 0:
        return [0 for _ in days], [0 for _ in days]
    averaged = {day: daily.get(day, 0) / suppliers for day in days}
    return _running(averaged, days)


def build_category_benchmark(supplier, period: ReportPeriod, subcategory_id: int | None = None) -> dict:
    options = _subcategory_options(supplier)
    selected = next((item for item in options if item.pk == subcategory_id), None)
    group_ids = _market_supplier_ids(supplier, selected.pk if selected else None)
    supplier_id = supplier.id
    minimum = _min_suppliers()
    group_size = len(group_ids)
    # Alone in the subcategory: show real values (comparative share is 100%).
    # Mask only when peers exist but the group is still too small for anonymity.
    masked = 1 < group_size < minimum
    selected_id = selected.pk if selected else None

    current = _visibility_block(supplier_id, group_ids, period.start, period.end, selected)
    appear_own, appear_category = _pair(supplier_id, current["appearances"])
    store_own, store_category = _pair(supplier_id, current["store_appearances"])
    product_appear_own = appear_own - store_own
    product_appear_category = appear_category - store_category
    store_visit_own, store_visit_category = _pair(supplier_id, current["store_visits"])
    product_visit_own, product_visit_category = _pair(supplier_id, current["product_visits"])
    visits_own = store_visit_own + product_visit_own
    visits_category = store_visit_category + product_visit_category

    orders_own = _order_qs(supplier_id, period.start, period.end, selected_id).count()
    quotes_own = _quote_qs(supplier_id, period.start, period.end, selected_id).count()
    samples_own = _sample_qs(supplier_id, period.start, period.end, selected_id).count()
    favorites_own = _favorite_qs(supplier_id, period.start, period.end, selected_id).count()
    orders_category, order_days = _group_model_count(Order, group_ids, period.start, period.end, selected_id)
    quotes_category, quote_days = _group_model_count(RFQRequest, group_ids, period.start, period.end, selected_id)
    samples_category, sample_days = _group_model_count(SampleRequest, group_ids, period.start, period.end, selected_id)
    favorites_category, favorite_days = _group_favorite_count(group_ids, period.start, period.end, selected_id)

    if selected is None:
        conversations_own = _new_conversations(supplier_id, period.start, period.end)
        conversation_days = _group_conversation_days(group_ids, period.start, period.end)
        conversations_category = sum(conversation_days.values())
    else:
        conversations_own = 0
        conversations_category = 0
        conversation_days = {}

    interactions_own = orders_own + quotes_own + samples_own + conversations_own + favorites_own
    interactions_category = (
        orders_category + quotes_category + samples_category + conversations_category + favorites_category
    )

    if selected is None:
        sponsored_own = _supplier_sponsored_views(supplier_id, period.start, period.end)
        sponsored_category = _sponsored_views(group_ids, period.start, period.end)
        sponsored_market = _sponsored_views(None, period.start, period.end)
    else:
        sponsored_own = sponsored_category = sponsored_market = 0

    market_appear = _marketplace_count(Event.EventType.SYSTEM_APPEAR_IN_SEARCH, period.start, period.end)
    market_store_appear = _marketplace_count(
        Event.EventType.SYSTEM_APPEAR_IN_SEARCH, period.start, period.end, source="suppliers"
    )
    market_product_appear = _marketplace_count(
        Event.EventType.SYSTEM_APPEAR_IN_SEARCH, period.start, period.end, source="products"
    )
    market_store_visits = _marketplace_count(
        Event.EventType.BUYER_SUPPLIER_VIEW, period.start, period.end, source="store_visits"
    )
    market_product_visits = _marketplace_count(Event.EventType.BUYER_PRODUCT_VIEW, period.start, period.end)
    market_visits = market_store_visits + market_product_visits
    market_orders = filter_created_in_local_period(Order.objects.all(), period.start, period.end).count()
    market_quotes = filter_created_in_local_period(RFQRequest.objects.all(), period.start, period.end).count()
    market_samples = filter_created_in_local_period(SampleRequest.objects.all(), period.start, period.end).count()
    market_favorites = filter_created_in_local_period(
        BuyerFavoriteProduct.objects.all(),
        period.start,
        period.end,
    ).count()
    market_conversations = _all_new_conversations(period.start, period.end)
    market_interactions = (
        market_orders + market_quotes + market_samples + market_conversations + market_favorites
    )

    visibility = [
        _volume_card("search_appearances", appear_category, appear_own, masked=masked),
        _percent_card("appearance_rate", appear_category, appear_own, market_appear, masked=masked),
        _volume_card("all_visits", visits_category, visits_own, masked=masked),
        _percent_card("visit_share", visits_category, visits_own, market_visits, masked=masked),
        _volume_card("interactions", interactions_category, interactions_own, masked=masked),
        _volume_card("sponsored_banner_views", sponsored_category, sponsored_own, masked=masked, action="sponsor"),
        _volume_card("store_appearances", store_category, store_own, masked=masked),
        _percent_card("store_appearance_rate", store_category, store_own, market_store_appear, masked=masked),
        _volume_card("store_visits", store_visit_category, store_visit_own, masked=masked),
        _percent_card("store_visit_share", store_visit_category, store_visit_own, market_store_visits, masked=masked),
        _volume_card("product_visits", product_visit_category, product_visit_own, masked=masked),
        _percent_card(
            "product_visit_share",
            product_visit_category,
            product_visit_own,
            market_product_visits,
            masked=masked,
        ),
        _volume_card("product_search_appearances", product_appear_category, product_appear_own, masked=masked),
        _percent_card(
            "product_appearance_share",
            product_appear_category,
            product_appear_own,
            market_product_appear,
            masked=masked,
        ),
    ]

    score_record = ensure_supplier_score(supplier_id)
    rank = _category_rank(supplier_id, group_ids, score_record.score_total)
    competitors = count_direct_competitors(supplier, cap=20)
    product_qs = Product.objects.filter(is_active=True, supplier_id__in=list(group_ids))
    own_products = supplier.products.filter(is_active=True)
    if selected_id:
        product_qs = product_qs.filter(subcategory_id=selected_id)
        own_products = own_products.filter(subcategory_id=selected_id)
    product_category = product_qs.count()
    product_own = own_products.count()
    followers_category = BuyerFavoriteSupplier.objects.filter(supplier_id__in=list(group_ids)).count()
    followers_own = BuyerFavoriteSupplier.objects.filter(supplier_id=supplier_id).count()
    own_spent = Decimal(str(wallet_payload(supplier)["spentTotal"]))
    category_spent = (
        PragmadinarWallet.objects.filter(supplier_id__in=list(group_ids)).aggregate(
            total=Sum("spent_total")
        )["total"]
        or Decimal("0")
    )
    own_delay, category_delay = _response_minutes(group_ids, supplier_id, period.start, period.end)

    activity = [
        _plain_card("category_rank", str(rank), masked=masked),
        _plain_card("competitors", str(competitors)),
        _volume_card("products", product_category, product_own, masked=masked),
        {
            "key": "availability",
            "label": BENCHMARK_LABELS["availability"],
            "pillTone": "volume",
            "value": "—",
            "pill": "—",
            "unavailable": True,
        },
        _response_card(own_delay, category_delay, masked=masked),
    ]
    if _show_pragmadinar():
        activity.append(_volume_card("pragmadinar", category_spent, own_spent, masked=masked))
    activity.extend(
        [
            _volume_card("orders", orders_category, orders_own, masked=masked),
            _volume_card("quotes", quotes_category, quotes_own, masked=masked),
            _volume_card("samples", samples_category, samples_own, masked=masked),
            _volume_card("conversations", conversations_category, conversations_own, masked=masked),
            _volume_card("followers", followers_category, followers_own, masked=masked),
            _volume_card("product_favorites", favorites_category, favorites_own, masked=masked),
        ]
    )

    days = _days(period.start, period.end)
    labels = [f"{day.day} {MONTH_SHORT[day.month - 1]}" for day in days]
    visit_own_daily = _merge_days(current["store_visit_daily"], current["product_visit_daily"])
    visit_group_daily = _merge_days(current["store_visit_group_daily"], current["product_visit_group_daily"])
    interaction_own_daily = _merge_days(
        _model_days(_order_qs(supplier_id, period.start, period.end, selected_id)),
        _model_days(_quote_qs(supplier_id, period.start, period.end, selected_id)),
        _model_days(_sample_qs(supplier_id, period.start, period.end, selected_id)),
        _model_days(_favorite_qs(supplier_id, period.start, period.end, selected_id)),
        _conversation_days(supplier_id, period.start, period.end) if selected is None else {},
    )
    interaction_group_daily = _merge_days(order_days, quote_days, sample_days, favorite_days, conversation_days)

    def _own_series(name: str, daily: dict) -> dict:
        detailed, cumulative = _running(daily, days)
        payload = {"name": name, "detailed": detailed, "cumulative": cumulative}
        return payload

    series = [
        _own_series("Apparition", current["appearance_daily"]),
        _own_series("Visite", visit_own_daily),
        _own_series("Interaction", interaction_own_daily),
        _score_named(supplier_id, days, score_record.score_total),
        _own_series("Pragmadinar", _pragmadinar_days(supplier_id, period.start, period.end)),
    ]
    if not masked:
        averages = [
            _average_running(current["appearance_group_daily"], days, group_size),
            _average_running(visit_group_daily, days, group_size),
            _average_running(interaction_group_daily, days, group_size),
            _score_average(group_ids, days),
            _average_running(_group_pragmadinar_days(group_ids, period.start, period.end), days, group_size),
        ]
        for item, (detailed, cumulative) in zip(series, averages):
            item["categoryDetailed"] = detailed
            item["categoryCumulative"] = cumulative

    return {
        "companyName": supplier.company_name,
        "period": period.as_dict(),
        "subcategories": [{"id": item.pk, "name": item.name_fr or item.name_en} for item in options],
        "subcategoryId": selected_id,
        "group": {
            "supplierCount": group_size,
            "minSuppliers": minimum,
            "masked": masked,
            "hint": ANONYMITY_HINT if masked else None,
        },
        "visibility": visibility,
        "activity": activity,
        "chart": {
            "labels": labels,
            "supplierName": supplier.company_name,
            "categoryLabel": None if masked else CATEGORY_AVERAGE,
            "series": series,
        },
    }


def _response_card(own_delay: float | None, category_delay: float | None, *, masked: bool) -> dict:
    payload = {
        "key": "response_time",
        "label": BENCHMARK_LABELS["response_time"],
        "pillTone": "none",
        "pill": None,
        "value": "—",
        "detail": None,
        "detailTone": None,
    }
    if masked:
        payload["hint"] = ANONYMITY_HINT
        payload["detail"] = "—"
        return payload
    payload["value"] = _format_delay(category_delay)
    payload["detail"] = _format_delay(own_delay)
    if own_delay is not None and category_delay is not None:
        payload["detailTone"] = "better" if own_delay < category_delay else "worse"
    return payload


def _score_named(supplier_id: int, days: list[date], current_score: int) -> dict:
    detailed, cumulative = _score_series(supplier_id, days, current_score)
    return {"name": "Variation de score", "detailed": detailed, "cumulative": cumulative}


def _score_average(group_ids: set[int], days: list[date]) -> tuple[list[float], list[float]]:
    scores = {
        row.supplier_id: row.score_total
        for row in SupplierScore.objects.filter(supplier_id__in=list(group_ids))
    }
    detailed_rows = []
    cumulative_rows = []
    for supplier_id in group_ids:
        detailed, cumulative = _score_series(supplier_id, days, scores.get(supplier_id, BASE_SCORE))
        detailed_rows.append(detailed)
        cumulative_rows.append(cumulative)
    return _mean_series(detailed_rows), _mean_series(cumulative_rows)


def _group_pragmadinar_days(supplier_ids: set[int], start: date, end: date) -> dict[date, float]:
    qs = filter_created_in_local_period(
        PragmadinarLedger.objects.filter(
            wallet__supplier_id__in=list(supplier_ids),
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
    daily: dict[date, float] = {}
    for row in rows:
        if row["day"]:
            daily[row["day"]] = float(row["spent"] or 0)
    return daily


def _all_new_conversations(start: date, end: date) -> int:
    started = Message.objects.values("supplier_id", "buyer_id").annotate(started=Min("created_at"))
    total = 0
    for row in started:
        if row["started"] is None:
            continue
        day = timezone.localtime(row["started"]).date()
        if start <= day <= end:
            total += 1
    return total
