"""
Main search logic combining taxonomy, tags, and text matching.
"""

from __future__ import annotations

from user.packaging_taxonomy import (
    build_synonym_groups,
    db_search_variants,
    expand_token_variants,
)
from user.search_normalize import (
    corpus_words,
    fold_text,
    levenshtein_distance,
    normalize_phrase,
    tokenize_query,
)

# Re-export normalization helpers used across the codebase.
__all__ = [
    'all_tokens_match',
    'corpus_words',
    'expand_query_tokens',
    'expand_search_token',
    'fold_text',
    'normalize_phrase',
    'phrase_in_text',
    'score_phrase_in_text',
    'score_token_in_text',
    'token_matches_text',
    'tokenize_query',
]

SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = build_synonym_groups()


def _max_edit_distance(token: str) -> int:
    length = len(token)
    if length <= 3:
        return 0
    if length <= 5:
        return 1
    return 2


def _synonym_variants(token: str, query_tokens: list[str] | None = None) -> set[str]:
    variants = expand_token_variants(token, query_tokens=query_tokens)
    folded = fold_text(token)
    if folded:
        variants.add(folded)

    for group in SYNONYM_GROUPS:
        if folded not in group:
            continue
        variants.update(group)
        break
    else:
        for group in SYNONYM_GROUPS:
            for term in group:
                if len(folded) >= 3 and (term.startswith(folded) or folded.startswith(term)):
                    variants.update(group)
                    break
                if len(folded) >= 4 and levenshtein_distance(folded, term) <= 1:
                    variants.update(group)
                    break

    return variants


def expand_query_tokens(
    tokens: list[str],
    *,
    for_db: bool = False,
) -> dict[str, set[str]]:
    """Map each query token to search variants using full-query context."""
    expanded: dict[str, set[str]] = {}
    for token in tokens:
        if for_db:
            expanded[token] = db_search_variants(token, query_tokens=tokens)
        else:
            expanded[token] = _synonym_variants(token, query_tokens=tokens)
    return expanded


def expand_search_token(
    token: str,
    query_tokens: list[str] | None = None,
    *,
    for_db: bool = False,
) -> set[str]:
    """Return normalized variants for DB prefilter or in-memory scoring."""
    folded = fold_text(token)
    if not folded:
        return set()
    if for_db:
        return db_search_variants(token, query_tokens=query_tokens)
    variants = _synonym_variants(token, query_tokens=query_tokens)
    if len(folded) >= 3:
        variants.add(folded[:3])
    if len(folded) >= 4:
        variants.add(folded[:4])
    return {item for item in variants if len(item) >= 2}


def phrase_in_text(phrase: str, text: str) -> bool:
    """Check whether a normalized phrase appears in normalized text."""
    normalized_phrase = normalize_phrase(phrase)
    if not normalized_phrase or not text:
        return False
    normalized_text = normalize_phrase(text)
    return normalized_phrase in normalized_text


def token_matches_text(
    token: str,
    text: str,
    *,
    query_tokens: list[str] | None = None,
) -> bool:
    """Match a token against free text (exact word, prefix, typo, synonym)."""
    folded_token = fold_text(token)
    if not folded_token:
        return False

    variants = _synonym_variants(token, query_tokens=query_tokens)
    words = corpus_words(text)
    if not words and text:
        words = [fold_text(text)]

    for word in words:
        for variant in variants:
            if word == variant:
                return True
            if len(variant) >= 3 and (word.startswith(variant) or variant.startswith(word)):
                return True
            if (
                len(variant) >= 4
                and len(word) >= 4
                and levenshtein_distance(word, variant) <= _max_edit_distance(variant)
            ):
                return True
    return False


def all_tokens_match(token: str, texts: list[str]) -> bool:
    corpus = ' '.join(text for text in texts if text)
    return token_matches_text(token, corpus)


def score_token_in_text(
    token: str,
    text: str,
    *,
    query_tokens: list[str] | None = None,
) -> int:
    """Higher score = better match (exact > prefix > fuzzy)."""
    folded_token = fold_text(token)
    if not folded_token or not text:
        return 0

    best = 0
    variants = _synonym_variants(token, query_tokens=query_tokens)
    for word in corpus_words(text):
        for variant in variants:
            if word == variant:
                best = max(best, 100)
            elif len(variant) >= 3 and word.startswith(variant):
                best = max(best, 75)
            elif len(variant) >= 3 and variant.startswith(word):
                best = max(best, 65)
            elif (
                len(variant) >= 4
                and len(word) >= 4
                and levenshtein_distance(word, variant) <= _max_edit_distance(variant)
            ):
                best = max(best, 55)
    return best


def score_phrase_in_text(phrase: str, text: str) -> int:
    """Phrase matches rank above isolated token matches."""
    if phrase_in_text(phrase, text):
        return 120
    return 0
