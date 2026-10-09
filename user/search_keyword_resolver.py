"""
Hierarchical search map: term → category → subcategory → attribute.

Supports ambiguous terms (several mappings) and multi-token context.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from user.models import Category, SearchTerm, SearchTermMapping
from user.search_term_normalize import normalize_search_term, query_phrases

MAP_SUBCATEGORY_RANK = 85
MAP_CATEGORY_RANK = 55
MAP_ATTRIBUTE_RANK = 40


@dataclass(frozen=True)
class SearchMapHit:
    term: str
    normalized_term: str
    term_type: str
    language: str
    is_ambiguous_term: bool
    category_id: int | None
    subcategory_id: int | None
    attribute_id: int | None
    attribute_value: str
    confidence: int
    priority: int
    context: str
    score: float


def _score(term: SearchTerm, mapping: SearchTermMapping) -> float:
    base = (term.weight or 50) * (mapping.confidence or 50) / 100.0
    base += (term.priority or 50) * 0.4 + (mapping.priority or 50) * 0.6
    if mapping.subcategory_id:
        base += MAP_SUBCATEGORY_RANK
    elif mapping.category_id:
        base += MAP_CATEGORY_RANK
    if mapping.attribute_id and mapping.attribute_value:
        base += MAP_ATTRIBUTE_RANK
    return base


def resolve_search_map(query: str, *, limit: int = 40) -> list[SearchMapHit]:
    stripped = (query or "").strip()
    if not stripped:
        return []

    phrases = query_phrases(stripped)
    if not phrases:
        return []

    terms = (
        SearchTerm.objects.filter(is_active=True, normalized_term__in=phrases)
        .prefetch_related(
            "mappings",
            "mappings__category",
            "mappings__subcategory",
            "mappings__attribute",
        )
    )
    term_by_norm = {t.normalized_term: t for t in terms}
    hits: list[SearchMapHit] = []

    for phrase in phrases:
        term = term_by_norm.get(phrase)
        if term is None:
            continue
        for mapping in term.mappings.all():
            if not mapping.is_active:
                continue
            hits.append(
                SearchMapHit(
                    term=term.term,
                    normalized_term=term.normalized_term,
                    term_type=term.term_type,
                    language=term.language,
                    is_ambiguous_term=term.is_ambiguous,
                    category_id=mapping.category_id,
                    subcategory_id=mapping.subcategory_id,
                    attribute_id=mapping.attribute_id,
                    attribute_value=(mapping.attribute_value or "").strip(),
                    confidence=mapping.confidence,
                    priority=mapping.priority,
                    context=(mapping.context or "").strip(),
                    score=_score(term, mapping),
                )
            )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def _single_token_query(query: str) -> bool:
    from user.search_engine import tokenize_query

    return len(tokenize_query(query.strip())) <= 1


def resolve_subcategories_for_crm(query: str) -> list[Category]:
    """
    Subcategories for CRM signals — conservative on ambiguous single-token queries.
    """
    hits = [h for h in resolve_search_map(query) if h.subcategory_id]
    if not hits:
        return []

    if _single_token_query(query) and hits[0].is_ambiguous_term:
        top = hits[0].score
        strong = [h for h in hits if h.score >= top - 5]
        if len({h.subcategory_id for h in strong}) > 1:
            return []

    chosen_ids: list[int] = []
    seen: set[int] = set()
    top_score = hits[0].score
    for hit in hits:
        if hit.subcategory_id in seen:
            continue
        if hit.score < top_score - 18:
            break
        seen.add(hit.subcategory_id)
        chosen_ids.append(hit.subcategory_id)

    return list(
        Category.objects.filter(pk__in=chosen_ids, parent__isnull=False).select_related(
            "parent"
        )
    )


def subcategory_ids_for_search_boost(query: str) -> list[int]:
    """Non-ambiguous subcategory hints for ranking (broader than CRM)."""
    hits = [h for h in resolve_search_map(query) if h.subcategory_id]
    if not hits:
        return []
    top = hits[0].score
    ids: list[int] = []
    for hit in hits:
        if hit.score < top - 25:
            break
        if hit.subcategory_id not in ids:
            ids.append(hit.subcategory_id)
    return ids[:5]


def invalidate_search_map_cache() -> None:
    pass
