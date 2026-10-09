"""Normalize buyer queries and dictionary terms (shared with product search)."""

from __future__ import annotations

from user.search_engine import normalize_phrase
from user.search_normalize import fold_text


def normalize_search_term(raw: str) -> str:
    return fold_text(normalize_phrase((raw or "").strip()))


def query_phrases(raw: str) -> list[str]:
    """Longest normalized n-grams first (multi-word context before single tokens)."""
    from user.search_engine import tokenize_query

    tokens = tokenize_query((raw or "").strip())
    if not tokens:
        key = normalize_search_term(raw)
        return [key] if key else []
    seen: set[str] = set()
    ordered: list[str] = []
    for start in range(len(tokens)):
        for end in range(start + 1, len(tokens) + 1):
            chunk = " ".join(tokens[start:end])
            key = normalize_search_term(chunk)
            if key and key not in seen:
                seen.add(key)
                ordered.append(key)
    ordered.sort(key=lambda item: (-len(item), item))
    return ordered
