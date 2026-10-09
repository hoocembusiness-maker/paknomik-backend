"""
Compares how similar two search strings or products are.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from django.core.cache import cache
from django.db.models import Q

from user.models import Category, Lexicon, Product, SearchTag, Supplier
from user.search_normalize import fold_text as _fold

VOCAB_CACHE_KEY = 'paknomik:search_vocabulary:v1'
VOCAB_CACHE_TTL = 3600
SYNONYM_SPLIT = re.compile(r'[,;|/]+')


def _split_synonyms(text: str) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in SYNONYM_SPLIT.split(text) if part.strip()]


def _build_vocabulary() -> list[dict]:
    entries: dict[str, dict] = {}

    def add(term: str, weight: int = 0) -> None:
        term = term.strip()
        if len(term) < 2:
            return
        key = _fold(term)
        existing = entries.get(key)
        if existing is None or weight > existing['weight']:
            entries[key] = {'display': term, 'weight': weight, 'fold': key}

    for row in SearchTag.objects.values(
        'tag', 'tag_fr', 'tag_ar', 'search_weight', 'synonyms'
    ):
        weight = row['search_weight'] or 0
        add(row['tag'], weight + 10)
        add(row['tag_fr'], weight + 10)
        add(row['tag_ar'], weight + 8)
        for synonym in _split_synonyms(row['synonyms'] or ''):
            add(synonym, weight + 5)

    for row in Lexicon.objects.values(
        'keyword',
        'french',
        'arabic',
        'synonyms',
        'alternative_names',
        'search_weight',
    ):
        weight = row['search_weight'] or 0
        add(row['keyword'], weight + 8)
        add(row['french'], weight + 8)
        add(row['arabic'], weight + 6)
        for synonym in _split_synonyms(row['synonyms'] or ''):
            add(synonym, weight + 5)
        for alt in _split_synonyms(row['alternative_names'] or ''):
            add(alt, weight + 5)

    for row in Category.objects.values('name_en', 'name_fr', 'name_ar'):
        add(row['name_en'], 6)
        add(row['name_fr'], 6)
        add(row['name_ar'], 5)

    for name in Product.objects.filter(is_active=True).values_list(
        'name', flat=True
    ).distinct():
        add(name, 4)

    for name in Supplier.objects.filter(is_active=True).values_list(
        'company_name', flat=True
    ).distinct():
        add(name, 3)

    return list(entries.values())


def get_vocabulary() -> list[dict]:
    cached = cache.get(VOCAB_CACHE_KEY)
    if cached is not None:
        return cached
    vocabulary = _build_vocabulary()
    cache.set(VOCAB_CACHE_KEY, vocabulary, VOCAB_CACHE_TTL)
    return vocabulary


def invalidate_vocabulary_cache() -> None:
    cache.delete(VOCAB_CACHE_KEY)


def term_in_vocabulary(query: str) -> bool:
    folded = _fold(query)
    if len(folded) < 2:
        return False
    if any(entry['fold'] == folded for entry in get_vocabulary()):
        return True
    return SearchTag.objects.filter(
        Q(tag__iexact=query) | Q(tag_fr__iexact=query) | Q(tag_ar__iexact=query)
    ).exists()


def _score_match(query_fold: str, entry: dict) -> float:
    term_fold = entry['fold']
    if query_fold == term_fold:
        return 2.0

    ratio = SequenceMatcher(None, query_fold, term_fold).ratio()
    if term_fold.startswith(query_fold) or query_fold.startswith(term_fold):
        ratio += 0.15

    prefix_len = min(3, len(query_fold), len(term_fold))
    if prefix_len and query_fold[:prefix_len] == term_fold[:prefix_len]:
        ratio += 0.08

    ratio += min(entry['weight'], 100) / 500
    return ratio


def find_similar_terms(
    query: str,
    limit: int = 5,
    min_score: float = 0.52,
) -> list[str]:
    """Return closest vocabulary terms for an unknown query or token."""
    query = query.strip()
    if len(query) < 2:
        return []

    query_fold = _fold(query)
    if term_in_vocabulary(query):
        return []

    scored: list[tuple[float, str]] = []
    seen: set[str] = set()

    for entry in get_vocabulary():
        score = _score_match(query_fold, entry)
        if score < min_score:
            continue
        display = entry['display']
        key = _fold(display)
        if key in seen:
            continue
        seen.add(key)
        scored.append((score, display))

    scored.sort(key=lambda item: (-item[0], item[1].lower()))
    return [term for _, term in scored[:limit]]


def find_similar_terms_for_query(query: str, limit: int = 5) -> list[str]:
    """Suggest corrections for each unknown word in a multi-word query."""
    query = query.strip()
    if len(query) < 2:
        return []

    if ' ' not in query:
        return find_similar_terms(query, limit=limit)

    suggestions: list[str] = []
    seen: set[str] = set()
    for word in query.split():
        if len(word) < 2 or term_in_vocabulary(word):
            continue
        for term in find_similar_terms(word, limit=2):
            key = _fold(term)
            if key not in seen:
                seen.add(key)
                suggestions.append(term)
            if len(suggestions) >= limit:
                return suggestions
    return suggestions


def prefix_term_suggestions(query: str, limit: int = 5) -> list[str]:
    """Autocomplete terms that start with the typed prefix."""
    query = query.strip()
    if len(query) < 2:
        return []

    folded_prefix = _fold(query)
    results: list[str] = []
    seen: set[str] = set()

    tags = SearchTag.objects.filter(
        Q(tag__istartswith=query)
        | Q(tag_fr__istartswith=query)
        | Q(tag_ar__istartswith=query)
    ).order_by('-search_weight', 'tag')[: limit * 2]

    for tag in tags:
        for label in (tag.tag_fr, tag.tag, tag.tag_ar):
            if not label:
                continue
            key = _fold(label)
            if key.startswith(folded_prefix) and key not in seen:
                seen.add(key)
                results.append(label)
                if len(results) >= limit:
                    return results

    return results


def get_term_suggestions(query: str, limit: int = 5) -> dict:
    """Prefix matches + fuzzy corrections for unknown terms."""
    query = query.strip()
    if len(query) < 2:
        return {'prefixTerms': [], 'similarTerms': [], 'didYouMean': None}

    prefix_terms = prefix_term_suggestions(query, limit=limit)
    similar_terms: list[str] = []

    if not term_in_vocabulary(query):
        similar_terms = find_similar_terms_for_query(query, limit=limit)

    prefix_fold = {_fold(term) for term in prefix_terms}
    similar_terms = [
        term for term in similar_terms if _fold(term) not in prefix_fold
    ]

    did_you_mean = similar_terms[0] if similar_terms else None
    return {
        'prefixTerms': prefix_terms,
        'similarTerms': similar_terms,
        'didYouMean': did_you_mean,
    }
