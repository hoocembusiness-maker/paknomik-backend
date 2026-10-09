"""
Filters supplier lists by search text and boutique display rules.
"""

from __future__ import annotations

from django.db.models import Case, IntegerField, Q, QuerySet, Value, When

from user.search_engine import (
    expand_query_tokens,
    expand_search_token,
    score_token_in_text,
    tokenize_query,
)

NAME_MATCH_RANK = 40
PRESENTATION_MATCH_RANK = 35
CATEGORY_MATCH_RANK = 25
LOCATION_MATCH_RANK = 15
PRODUCT_CATEGORY_MATCH_RANK = 12
BOUTIQUE_SEARCH_HEADER_TIERS = ("extra", "central", "mega")


def supplier_has_boutique_search_priority(supplier) -> bool:
    settings_obj = getattr(supplier, "settings", None)
    if settings_obj is None:
        return False
    raw = getattr(settings_obj, "page_design", None) or {}
    if str(raw.get("useStoreHeaderInSearch") or "").lower() not in {"true", "1", "yes"}:
        return False
    return str(raw.get("storeHeaderTier") or "standard").lower() in BOUTIQUE_SEARCH_HEADER_TIERS


def boutique_search_priority_q(*, supplier_lookup: str = "") -> Q:
    prefix = f"{supplier_lookup}settings__page_design__"
    return Q(**{f"{prefix}useStoreHeaderInSearch__in": ["true", "True", "1", "yes"]}) & Q(
        **{f"{prefix}storeHeaderTier__in": list(BOUTIQUE_SEARCH_HEADER_TIERS)}
    )


def annotate_boutique_search_priority(queryset: QuerySet, *, through_supplier: bool = False):
    lookup = "supplier__" if through_supplier else ""
    return queryset.annotate(
        _boutique_search_priority=Case(
            When(boutique_search_priority_q(supplier_lookup=lookup), then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    )


def _supplier_variant_q(variant: str) -> Q:
    return (
        Q(company_name__icontains=variant)
        | Q(presentation__icontains=variant)
        | Q(address__icontains=variant)
        | Q(interested_categories__name_en__icontains=variant)
        | Q(interested_categories__name_fr__icontains=variant)
        | Q(interested_categories__name_ar__icontains=variant)
        | Q(interested_subcategories__name_en__icontains=variant)
        | Q(interested_subcategories__name_fr__icontains=variant)
        | Q(interested_subcategories__name_ar__icontains=variant)
        | Q(products__name__icontains=variant)
        | Q(products__description__icontains=variant)
        | Q(products__category__name_en__icontains=variant)
        | Q(products__category__name_fr__icontains=variant)
        | Q(products__search_tags__tag__icontains=variant)
        | Q(products__search_tags__tag_fr__icontains=variant)
    )


def supplier_token_prefilter_q(tokens: list[str]) -> Q:
    expanded = expand_query_tokens(tokens, for_db=True)
    combined = Q()
    for token in tokens:
        token_q = Q()
        for variant in expanded.get(
            token, expand_search_token(token, query_tokens=tokens, for_db=True)
        ):
            token_q |= _supplier_variant_q(variant)
        combined &= token_q
    return combined


def supplier_text_search_q(query: str) -> Q:
    tokens = tokenize_query(query)
    if tokens:
        return supplier_token_prefilter_q(tokens)
    stripped = query.strip()
    if not stripped:
        return Q()
    return _supplier_variant_q(stripped)


def _supplier_searchable_text(supplier) -> dict[str, str]:
    categories = " ".join(
        supplier.interested_categories.values_list("name_fr", flat=True)
    )
    categories += " " + " ".join(
        supplier.interested_categories.values_list("name_en", flat=True)
    )
    subcategories = " ".join(
        supplier.interested_subcategories.values_list("name_fr", flat=True)
    )
    subcategories += " " + " ".join(
        supplier.interested_subcategories.values_list("name_en", flat=True)
    )
    product_categories = " ".join(
        supplier.products.filter(is_active=True).values_list(
            "category__name_fr", flat=True
        )
    )
    location = supplier.get_wilaya_display() if supplier.wilaya else (supplier.address or "")
    tagline = supplier.get_type_display() if supplier.type else ""

    return {
        "name": supplier.company_name or "",
        "presentation": supplier.presentation or "",
        "categories": f"{categories} {subcategories} {product_categories}".strip(),
        "location": location,
        "tagline": tagline,
    }


def score_supplier_match(supplier, tokens: list[str]) -> int:
    if not tokens:
        return 0

    fields = _supplier_searchable_text(supplier)
    weights = {
        "name": NAME_MATCH_RANK,
        "presentation": PRESENTATION_MATCH_RANK,
        "categories": CATEGORY_MATCH_RANK,
        "location": LOCATION_MATCH_RANK,
        "tagline": CATEGORY_MATCH_RANK,
    }

    total = 0
    for token in tokens:
        token_best = 0
        for field_name, text in fields.items():
            field_score = score_token_in_text(token, text, query_tokens=tokens)
            if field_score:
                weighted = int(field_score * weights[field_name] / 100)
                token_best = max(token_best, weighted)
        if token_best == 0:
            return 0
        total += token_best
    return total


def filter_suppliers_by_text(queryset: QuerySet, query: str) -> QuerySet:
    query = (query or "").strip()
    if not query:
        return queryset

    tokens = tokenize_query(query)
    queryset = queryset.select_related("settings").filter(supplier_text_search_q(query)).distinct()

    if not tokens:
        return queryset.filter(company_name__icontains=query).distinct()

    candidates = list(
        queryset.prefetch_related(
            "interested_categories",
            "interested_subcategories",
            "products__category",
        )
    )
    scored: list[tuple[int, object]] = []
    for supplier in candidates:
        score = score_supplier_match(supplier, tokens)
        if score > 0:
            scored.append((score, supplier))

    if not scored:
        return queryset.none()

    scored.sort(
        key=lambda item: (
            -int(supplier_has_boutique_search_priority(item[1])),
            -item[0],
            -int(item[1].is_featured),
            item[1].company_name.lower(),
        )
    )
    ordered_ids = [supplier.pk for _, supplier in scored]
    preserved = Case(
        *[When(pk=pk, then=pos) for pos, pk in enumerate(ordered_ids)],
        output_field=IntegerField(),
    )
    return queryset.filter(pk__in=ordered_ids).order_by(preserved)
