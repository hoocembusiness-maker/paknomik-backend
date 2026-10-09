"""
Filters product lists by search text.
"""

from __future__ import annotations

from django.db import connection
from django.db.models import Case, IntegerField, Q, QuerySet, Value, When

from user.product_markets import (
    market_search_terms,
    matching_market_ids_for_query,
    normalize_target_markets,
    target_market_labels,
)
from user.search_normalize import arabic_icontains_variants, french_icontains_variants
from user.supplier_search import supplier_has_boutique_search_priority
from user.search_engine import (
    expand_query_tokens,
    expand_search_token,
    normalize_phrase,
    score_phrase_in_text,
    score_token_in_text,
    tokenize_query,
)

# User priority: description + target markets.
PHRASE_MATCH_RANK = 90
MARKET_MATCH_RANK = 70
DESCRIPTION_MATCH_RANK = 50
NAME_MATCH_RANK = 35
TAG_MATCH_RANK = 22
CATEGORY_MATCH_RANK = 14
SUPPLIER_MATCH_RANK = 10
LOCATION_MATCH_RANK = 8


def _target_markets_q(market_id: str) -> Q:
    """Match products tagged with a target market (SQLite + PostgreSQL)."""
    if connection.vendor == "sqlite":
        return Q(target_markets__icontains=f'"{market_id}"')
    return Q(target_markets__contains=[market_id])


def _variant_field_q(variant: str) -> Q:
    return (
        Q(name__icontains=variant)
        | Q(name_ar__icontains=variant)
        | Q(description__icontains=variant)
        | Q(category__name_en__icontains=variant)
        | Q(category__name_fr__icontains=variant)
        | Q(category__name_ar__icontains=variant)
        | Q(category__slug__icontains=variant)
        | Q(subcategory__name_en__icontains=variant)
        | Q(subcategory__name_fr__icontains=variant)
        | Q(subcategory__name_ar__icontains=variant)
        | Q(subcategory__slug__icontains=variant)
        | Q(search_tags__tag__icontains=variant)
        | Q(search_tags__tag_fr__icontains=variant)
        | Q(search_tags__tag_ar__icontains=variant)
        | Q(custom_keywords__icontains=variant)
        | Q(supplier__company_name__icontains=variant)
        | Q(supplier__address__icontains=variant)
    )


def target_market_search_q(query: str) -> Q:
    combined = Q()
    for market_id in matching_market_ids_for_query(query):
        combined |= _target_markets_q(market_id)
    for token in tokenize_query(query):
        for market_id in matching_market_ids_for_query(token):
            combined |= _target_markets_q(market_id)
    return combined


def product_token_prefilter_q(tokens: list[str]) -> Q:
    """Each token must match at least one field variant (AND across tokens)."""
    if not tokens:
        return Q()

    expanded = expand_query_tokens(tokens, for_db=True)
    combined = Q()
    for token in tokens:
        token_q = target_market_search_q(token) | _variant_field_q(token)
        for variant in expanded.get(
            token, expand_search_token(token, query_tokens=tokens, for_db=True)
        ):
            token_q |= _variant_field_q(variant)
        combined &= token_q
    return combined


def _phrase_search_q(phrase: str) -> Q:
    stripped = phrase.strip()
    if not stripped:
        return Q()

    combined = (
        _variant_field_q(stripped)
        | Q(name__icontains=stripped)
        | Q(name_ar__icontains=stripped)
        | Q(description__icontains=stripped)
    )
    for variant in french_icontains_variants(stripped):
        combined |= Q(name__icontains=variant) | Q(description__icontains=variant)
    for variant in arabic_icontains_variants(stripped):
        combined |= (
            Q(name_ar__icontains=variant)
            | Q(description__icontains=variant)
            | Q(search_tags__tag_ar__icontains=variant)
        )
    return combined


def product_text_search_q(query: str) -> Q:
    stripped = query.strip()
    if not stripped:
        return Q()

    tokens = tokenize_query(stripped)
    if not tokens:
        return _variant_field_q(stripped) | target_market_search_q(stripped)

    combined = product_token_prefilter_q(tokens)
    if len(tokens) >= 2:
        combined |= _phrase_search_q(stripped)
        latin_tokens = [token for token in tokens if token.isascii()]
        arabic_tokens = [token for token in tokens if not token.isascii()]
        if latin_tokens and arabic_tokens:
            cross_q = Q()
            for latin in latin_tokens:
                for arabic in arabic_tokens:
                    for latin_variant in french_icontains_variants(latin):
                        cross_q |= Q(name__icontains=latin_variant, name_ar__icontains=arabic)
                        cross_q |= Q(
                            category__name_fr__icontains=latin_variant,
                            category__name_ar__icontains=arabic,
                        )
                        cross_q |= Q(
                            search_tags__tag_fr__icontains=latin_variant,
                            search_tags__tag_ar__icontains=arabic,
                        )
                    for arabic_variant in arabic_icontains_variants(arabic):
                        cross_q |= Q(name__icontains=latin, name_ar__icontains=arabic_variant)
                        cross_q |= Q(
                            category__name_fr__icontains=latin,
                            category__name_ar__icontains=arabic_variant,
                        )
                        cross_q |= Q(
                            search_tags__tag_fr__icontains=latin,
                            search_tags__tag_ar__icontains=arabic_variant,
                        )
            combined |= cross_q
    return combined


def _market_labels_text(market_ids: list[str] | None) -> str:
    labels = target_market_labels(market_ids)
    terms: list[str] = list(labels)
    for market_id in normalize_target_markets(market_ids):
        terms.extend(market_search_terms(market_id))
    return " ".join(terms)


def annotate_product_search_rank(
    queryset: QuerySet,
    query: str,
    tokens: list[str] | None = None,
) -> QuerySet:
    q = query.strip()
    if not q:
        return queryset

    token_list = tokens or tokenize_query(q)
    rank = Value(0, output_field=IntegerField())

    normalized_q = normalize_phrase(q)
    if normalized_q and len(normalized_q.split()) >= 2:
        rank = rank + Case(
            When(description__icontains=q, then=Value(PHRASE_MATCH_RANK)),
            When(name__icontains=q, then=Value(PHRASE_MATCH_RANK - 10)),
            default=Value(0),
            output_field=IntegerField(),
        )

    rank = rank + Case(
        When(name__icontains=q, then=Value(NAME_MATCH_RANK)),
        When(name_ar__icontains=q, then=Value(NAME_MATCH_RANK)),
        default=Value(0),
        output_field=IntegerField(),
    )
    for token in token_list or [q]:
        rank = rank + Case(
            When(description__icontains=token, then=Value(DESCRIPTION_MATCH_RANK)),
            default=Value(0),
            output_field=IntegerField(),
        )
        for variant in expand_search_token(token):
            if variant == token:
                continue
            rank = rank + Case(
                When(description__icontains=variant, then=Value(DESCRIPTION_MATCH_RANK - 8)),
                default=Value(0),
                output_field=IntegerField(),
            )

    rank = rank + Case(
        When(
            Q(search_tags__tag__icontains=q)
            | Q(search_tags__tag_fr__icontains=q)
            | Q(search_tags__tag_ar__icontains=q)
            | Q(custom_keywords__icontains=q),
            then=Value(TAG_MATCH_RANK),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )
    rank = rank + Case(
        When(
            Q(category__name_en__icontains=q)
            | Q(category__name_fr__icontains=q)
            | Q(subcategory__name_en__icontains=q)
            | Q(subcategory__name_fr__icontains=q),
            then=Value(CATEGORY_MATCH_RANK),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )
    rank = rank + Case(
        When(supplier__company_name__icontains=q, then=Value(SUPPLIER_MATCH_RANK)),
        default=Value(0),
        output_field=IntegerField(),
    )

    market_ids = matching_market_ids_for_query(q)
    for token in token_list:
        for market_id in matching_market_ids_for_query(token):
            if market_id not in market_ids:
                market_ids.append(market_id)

    for market_id in market_ids:
        if connection.vendor == "sqlite":
            rank = rank + Case(
                When(
                    target_markets__icontains=f'"{market_id}"',
                    then=Value(MARKET_MATCH_RANK),
                ),
                default=Value(0),
                output_field=IntegerField(),
            )
        else:
            rank = rank + Case(
                When(
                    target_markets__contains=[market_id],
                    then=Value(MARKET_MATCH_RANK),
                ),
                default=Value(0),
                output_field=IntegerField(),
            )

    return queryset.annotate(search_rank=rank).order_by(
        "-search_rank", "-is_featured", "-created_at"
    )


def score_product_match(product, tokens: list[str], *, query: str = '') -> int:
    """Python-side relevance score; returns 0 if any token fails to match."""
    if not tokens:
        return 0

    tag_text = ' '.join(
        (tag.tag_fr or tag.tag or tag.tag_ar or '')
        for tag in product.search_tags.all()
    )
    market_text = _market_labels_text(product.target_markets)
    supplier = product.supplier
    location = ""
    if supplier:
        location = supplier.get_wilaya_display() if supplier.wilaya else (supplier.address or "")

    category_text = ' '.join(
        filter(
            None,
            [
                getattr(product.category, 'name_fr', ''),
                getattr(product.category, 'name_en', ''),
                getattr(product.category, 'name_ar', ''),
                getattr(product.subcategory, 'name_fr', ''),
                getattr(product.subcategory, 'name_en', ''),
                getattr(product.subcategory, 'name_ar', ''),
            ],
        )
    )

    fields = {
        'description': product.description or '',
        'markets': market_text,
        'name': product.name or '',
        'name_ar': product.name_ar or '',
        'tags': tag_text,
        'category': category_text,
        'supplier': supplier.company_name if supplier else '',
        'location': location,
    }

    weights = {
        'description': DESCRIPTION_MATCH_RANK,
        'markets': MARKET_MATCH_RANK,
        'name': NAME_MATCH_RANK,
        'name_ar': NAME_MATCH_RANK,
        'tags': TAG_MATCH_RANK,
        'category': CATEGORY_MATCH_RANK,
        'supplier': SUPPLIER_MATCH_RANK,
        'location': LOCATION_MATCH_RANK,
    }

    total = 0
    if query and len(tokens) >= 2:
        phrase_bonus = 0
        for field_name, text in fields.items():
            phrase_score = score_phrase_in_text(query, text)
            if phrase_score:
                phrase_bonus = max(
                    phrase_bonus,
                    int(phrase_score * weights[field_name] / 100),
                )
        total += phrase_bonus

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

    if query.strip():
        from user.search_keyword_resolver import resolve_search_map

        sub_id = getattr(product, "subcategory_id", None)
        if sub_id:
            for index, hit in enumerate(resolve_search_map(query, limit=10)):
                if hit.subcategory_id == sub_id:
                    total += max(12, 36 - index * 4)
                    break
    return total


def filter_products_by_text(
    queryset: QuerySet,
    query: str,
    *,
    include_supplier_name: bool = True,
    rank_results: bool = True,
) -> QuerySet:
    query = (query or "").strip()
    if not query:
        return queryset

    tokens = tokenize_query(query)
    base_queryset = queryset
    q_obj = product_text_search_q(query)
    if include_supplier_name and not tokens:
        q_obj |= Q(supplier__company_name__icontains=query)

    filtered_queryset = base_queryset.filter(q_obj).distinct()

    if not rank_results:
        return filtered_queryset

    if not tokens:
        return annotate_product_search_rank(filtered_queryset, query, tokens=[])

    def _gather_scored(source_queryset: QuerySet) -> list[tuple[int, object]]:
        rows: list[tuple[int, object]] = []
        for product in source_queryset.select_related(
            'supplier', 'category', 'subcategory',
        ).prefetch_related('search_tags'):
            score = score_product_match(product, tokens, query=query)
            if score > 0:
                rows.append((score, product))
        return rows

    scored = _gather_scored(filtered_queryset)

    if not scored and tokens:
        loose_q = Q()
        for token in tokens:
            loose_q |= _variant_field_q(token)
            for variant in expand_search_token(token, query_tokens=tokens, for_db=True):
                loose_q |= _variant_field_q(variant)
        scored = _gather_scored(base_queryset.filter(loose_q).distinct())

    if not scored:
        return base_queryset.none()

    scored.sort(
        key=lambda item: (
            -int(supplier_has_boutique_search_priority(item[1].supplier)),
            -item[0],
            -int(item[1].is_featured),
            -item[1].created_at.timestamp(),
        )
    )
    ordered_ids = [product.pk for _, product in scored]
    preserved = Case(
        *[When(pk=pk, then=pos) for pos, pk in enumerate(ordered_ids)],
        output_field=IntegerField(),
    )
    return base_queryset.filter(pk__in=ordered_ids).order_by(preserved)
