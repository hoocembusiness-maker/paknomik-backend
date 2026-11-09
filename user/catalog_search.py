"""Search assembly for the new catalogue. Organic order never includes sponsored rows."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Q
from django.utils import timezone

from user.catalog_taxonomy import (
    ALTERNATIVES_BILLABLE,
    ALTERNATIVES_MAX,
    ALTERNATIVES_MIN_RESULTS,
    LEVEL_WEIGHT,
    OFFER_STOCK,
    OFFER_SUR_MESURE,
    PAGE_SIZE,
    action_label,
    badge_label,
    filters_match_count,
    gamme_score,
    primary_action,
    quality_score,
    rank_organic,
    reactivity_score,
    relevance_score,
    secondary_action,
    translate_filters,
    variant_summary,
    GammeCard,
)
from user.models import Campaign, Product, ProductType, SupplierScore, TypeAlternative


def _variants(product: Product) -> list[dict]:
    return [
        {
            "id": variant.pk,
            "axes": variant.axes or {},
            "price_from": variant.price_from,
            "moq": variant.moq,
            "is_available": variant.is_available,
            "sku": variant.sku,
        }
        for variant in product.variants.all()
    ]


def _score_product(product: Product, *, query: str, filters: dict, type_id: int | None):
    schema = list(product.product_type.attribute_schema or [])
    variants = _variants(product)
    matched, total = filters_match_count(
        schema, product.attributes or {}, variants, filters
    )
    if matched != total:
        return None
    relevance = relevance_score(
        name=product.name,
        description=product.description or "",
        query=query,
        filters_matched=matched,
        filters_total=total,
        type_match=type_id is None or product.product_type_id == type_id,
    )
    required = [item for item in schema if item.get("required")]
    filled = 0
    for item in required:
        key = item["key"]
        if key in (product.attributes or {}):
            filled += 1
        elif variants and all(key in (row.get("axes") or {}) for row in variants):
            filled += 1
    record = SupplierScore.objects.filter(supplier_id=product.supplier_id).only(
        "score_responsivity"
    ).first()
    raw = record.score_responsivity if record else None
    quality = quality_score(
        required_filled=filled,
        required_total=len(required),
        description_length=len(product.description or ""),
        has_photo=bool(list(product.images.all())),
        has_sheet=bool(product.technical_sheet),
    )
    return gamme_score(
        relevance=relevance,
        quality=quality,
        reactivity=reactivity_score(raw),
    )


def _queryset():
    return (
        Product.objects.filter(
            is_active=True,
            supplier__is_active=True,
            supplier__is_ghost=False,
            product_type__isnull=False,
        )
        .exclude(offer_mode="")
        .select_related("supplier", "product_type", "product_type__family")
        .prefetch_related("variants", "images")
    )


def _cards_for(products, *, query, filters, type_id, weight=1.0):
    cards = []
    for product in products:
        score = _score_product(product, query=query, filters=filters, type_id=type_id)
        if score is None:
            continue
        cards.append(
            GammeCard(
                product_id=product.pk,
                supplier_id=product.supplier_id,
                type_id=product.product_type_id,
                score=score * weight,
                is_flagship=product.is_flagship,
            )
        )
    return cards


def _money(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(str(value)):.2f}"


def _present(product: Product, collapsed, *, reason, billable: bool) -> dict[str, Any]:
    schema = list(product.product_type.attribute_schema or [])
    variants = _variants(product)
    prices = [
        row["price_from"]
        for row in variants
        if row["price_from"] is not None and row["is_available"]
    ]
    action = primary_action(product.offer_mode)
    secondary = secondary_action(product.offer_mode, product.is_mixed)
    return {
        "id": product.pk,
        "name": product.name,
        "supplierId": product.supplier_id,
        "supplierName": product.supplier.company_name,
        "supplierKind": product.supplier.kind or "",
        "typeId": product.product_type_id,
        "typeLabel": product.product_type.name_fr,
        "familyId": product.product_type.family_id,
        "familyLabel": product.product_type.family.name_fr,
        "offerMode": product.offer_mode,
        "badge": badge_label(product.offer_mode),
        "primaryAction": action,
        "primaryActionLabel": action_label(action),
        "secondaryAction": secondary,
        "secondaryActionLabel": action_label(secondary),
        "summary": variant_summary(variants, schema),
        "priceFrom": _money(min(prices)) if prices else None,
        "otherModelsCount": len(collapsed.other_ids),
        "otherModelIds": collapsed.other_ids,
        "score": round(collapsed.score, 4),
        "reason": reason,
        "sponsored": False,
        "billable": billable,
        "attributes": product.attributes or {},
        "variants": [
            {
                "id": row["id"],
                "sku": row["sku"],
                "axes": row["axes"],
                "priceFrom": _money(row["price_from"]),
                "moq": row["moq"],
                "available": row["is_available"],
            }
            for row in variants
        ],
    }


def _alternative_edges(product_type: ProductType):
    outgoing = TypeAlternative.objects.filter(from_type=product_type).select_related(
        "to_type"
    )
    incoming = TypeAlternative.objects.filter(
        to_type=product_type, bidirectional=True
    ).select_related("from_type")
    edges = []
    for row in outgoing:
        edges.append((row.to_type, row.level, row.attribute_map or {}, row.reason))
    for row in incoming:
        inverted = {value: key for key, value in (row.attribute_map or {}).items()}
        edges.append((row.from_type, row.level, inverted, row.reason))
    return edges


def _alternatives(source, *, query, filters, on_date, session_slice, offer_mode):
    collected = []
    reasons = {}
    products = {}
    for target, level, attribute_map, reason in _alternative_edges(source):
        translated = translate_filters(
            filters,
            attribute_map,
            list(source.attribute_schema or []),
            list(target.attribute_schema or []),
        )
        if translated.blocked:
            continue
        weight = LEVEL_WEIGHT.get(level, LEVEL_WEIGHT["PROCHE"])
        qs = _queryset().filter(product_type=target)
        if offer_mode in {OFFER_STOCK, OFFER_SUR_MESURE}:
            qs = qs.filter(offer_mode=offer_mode)
        if query:
            qs = qs.filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            )
        found = list(qs)
        cards = _cards_for(
            found,
            query=query,
            filters=translated.filters,
            type_id=target.pk,
            weight=weight,
        )
        for card in cards:
            reasons[card.product_id] = reason
        for product in found:
            products[product.pk] = product
        collected.extend(cards)
    ranked, _total = rank_organic(
        collected,
        page=1,
        page_size=ALTERNATIVES_MAX,
        on_date=on_date,
        session_slice=session_slice,
    )
    rows = []
    for item in ranked[:ALTERNATIVES_MAX]:
        product = products.get(item.product_id)
        if product is None:
            continue
        rows.append(
            _present(
                product,
                item,
                reason=reasons.get(item.product_id, ""),
                billable=ALTERNATIVES_BILLABLE,
            )
        )
    return rows


_VITRINE = {
    Campaign.Format.VITRINE_EXTRA,
    Campaign.Format.VITRINE_CENTRAL,
    Campaign.Format.VITRINE_MEGA,
}
_CARD = {Campaign.Format.CARD_PASSPORT, Campaign.Format.CARD_HORIZONTAL}
_BANNER = {Campaign.Format.BANNER}


def _slot(campaign_format: str) -> str | None:
    if campaign_format in _VITRINE:
        return "vitrines"
    if campaign_format in _CARD or campaign_format == Campaign.Format.PRIORITY_SUBCATEGORY:
        return "cards"
    if campaign_format in _BANNER:
        return "banners"
    return None


def sponsored_slots(type_id: int | None) -> dict[str, list]:
    """Distinct sponsored placements. They do not reorder the organic list."""
    empty = {"vitrines": [], "cards": [], "banners": []}
    if not type_id:
        return empty
    product_type = ProductType.objects.filter(pk=type_id).first()
    if product_type is None:
        return empty
    now = timezone.now()
    campaigns = (
        Campaign.objects.filter(status=Campaign.Status.ACTIVE)
        .filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gte=now),
        )
        .select_related("supplier")
    )
    buckets = {"vitrines": [], "cards": [], "banners": []}
    seen = {"vitrines": set(), "cards": set(), "banners": set()}
    legacy_id = product_type.legacy_category_id
    for campaign in campaigns:
        slot = _slot(campaign.campaign_format)
        if slot is None:
            continue
        if campaign.campaign_format == Campaign.Format.PRIORITY_SUBCATEGORY:
            if not legacy_id or campaign.subcategory_id != legacy_id:
                continue
        if campaign.supplier_id in seen[slot]:
            continue
        seen[slot].add(campaign.supplier_id)
        buckets[slot].append(
            {
                "campaignId": campaign.pk,
                "supplierId": campaign.supplier_id,
                "supplierName": campaign.supplier.company_name,
                "format": campaign.campaign_format,
                "label": "Sponsorisé",
                "typeId": type_id,
            }
        )
    return buckets


def search_catalog(
    *,
    query: str = "",
    type_id: int | None = None,
    family_id: int | None = None,
    offer_mode: str = "",
    filters: dict | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE,
    on_date: date | None = None,
    session_slice: int = 0,
) -> dict[str, Any]:
    filters = filters or {}
    today = on_date or timezone.localdate()
    base = _queryset()
    if family_id:
        base = base.filter(product_type__family_id=family_id)
    if offer_mode in {OFFER_STOCK, OFFER_SUR_MESURE}:
        base = base.filter(offer_mode=offer_mode)
    if query:
        base = base.filter(Q(name__icontains=query) | Q(description__icontains=query))

    main_qs = base.filter(product_type_id=type_id) if type_id else base
    main_products = list(main_qs)
    main_cards = _cards_for(
        main_products, query=query, filters=filters, type_id=type_id
    )
    page_rows, total = rank_organic(
        main_cards,
        page=page,
        page_size=page_size,
        on_date=today,
        session_slice=session_slice,
    )
    by_id = {product.pk: product for product in main_products}
    results = []
    for item in page_rows:
        product = by_id.get(item.product_id)
        if product is not None:
            results.append(_present(product, item, reason=None, billable=True))

    alternatives = []
    if type_id:
        source = ProductType.objects.filter(pk=type_id).first()
        if source is not None:
            alternatives = _alternatives(
                source,
                query=query,
                filters=filters,
                on_date=today,
                session_slice=session_slice,
                offer_mode=offer_mode,
            )
    return {
        "results": results,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "alternatives": alternatives,
        "alternativesPromoted": total < ALTERNATIVES_MIN_RESULTS,
        "alternativesBillable": ALTERNATIVES_BILLABLE,
        "sponsored": sponsored_slots(type_id),
    }


def quote_prefill(product_type: ProductType) -> dict[str, Any]:
    suggestions = []
    for target, level, _attribute_map, reason in _alternative_edges(product_type):
        suggestions.append(
            {
                "typeId": target.pk,
                "label": target.name_fr,
                "level": level,
                "reason": reason,
            }
        )
    return {
        "typeId": product_type.pk,
        "typeLabel": product_type.name_fr,
        "familyLabel": product_type.family.name_fr,
        "technicalCharacteristics": list(product_type.attribute_schema or []),
        "alternatives": suggestions,
        "notice": "Votre besoin peut aussi être couvert par…",
        "autoSend": False,
    }


def parse_filters(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Filtres illisibles.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Filtres illisibles.")
        return parsed
    raise ValueError("Filtres illisibles.")
