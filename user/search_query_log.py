"""Persist searches with no product hits."""

from __future__ import annotations

from user.models import SearchQueryMiss
from user.search_keyword_resolver import resolve_search_map
from user.search_term_normalize import normalize_search_term


def log_search_miss(
    *,
    query: str,
    results_count: int,
    buyer_id: int | None = None,
    source: str = "products",
) -> None:
    original = (query or "").strip()
    if not original or results_count > 0:
        return
    normalized = normalize_search_term(original)
    if not normalized:
        return

    hits = resolve_search_map(original, limit=3)
    suggested = next((h.subcategory_id for h in hits if h.subcategory_id), None)
    language_hint = hits[0].language if hits else ""

    SearchQueryMiss.objects.create(
        original_query=original[:255],
        normalized_query=normalized[:255],
        language_hint=language_hint[:8],
        results_count=results_count,
        suggested_subcategory_id=suggested,
        buyer_id=buyer_id,
        source=source[:32],
    )
