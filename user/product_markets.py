"""
Target market labels (France, MENA, etc.) for products.
"""

from __future__ import annotations

from user.search_engine import fold_text

TARGET_MARKET_OPTIONS: tuple[dict[str, str | tuple[str, ...]], ...] = (
    {
        "id": "restaurant",
        "labelFr": "Restaurant",
        "labelEn": "Restaurant",
        "searchTerms": (
            "restaurant",
            "restauration",
            "food service",
            "traiteur",
            "brasserie",
            "hôtellerie",
        ),
    },
    {
        "id": "cafe",
        "labelFr": "Café",
        "labelEn": "Café",
        "searchTerms": ("cafe", "café", "coffee", "bar", "salon de thé"),
    },
    {
        "id": "restauration-rapide",
        "labelFr": "Restauration rapide",
        "labelEn": "Fast food",
        "searchTerms": (
            "restauration rapide",
            "fast food",
            "fast-food",
            "quick service",
            "snack",
        ),
    },
    {
        "id": "alimentaire",
        "labelFr": "Agroalimentaire",
        "labelEn": "Food & beverage",
        "searchTerms": (
            "alimentaire",
            "agroalimentaire",
            "food",
            "beverage",
            "boisson",
            "épicerie",
        ),
    },
    {
        "id": "pharma",
        "labelFr": "Pharma",
        "labelEn": "Pharma",
        "searchTerms": (
            "pharma",
            "pharmaceutique",
            "pharmacie",
            "médicament",
            "santé",
        ),
    },
    {
        "id": "cosmetique",
        "labelFr": "Cosmétique",
        "labelEn": "Cosmetics",
        "searchTerms": (
            "cosmetique",
            "cosmétique",
            "beauté",
            "parfumerie",
            "soin",
        ),
    },
    {
        "id": "commerce",
        "labelFr": "Commerce & retail",
        "labelEn": "Retail",
        "searchTerms": (
            "commerce",
            "retail",
            "détail",
            "magasin",
            "supermarché",
            "grande surface",
        ),
    },
    {
        "id": "industrie",
        "labelFr": "Industrie",
        "labelEn": "Industrial",
        "searchTerms": (
            "industrie",
            "industrial",
            "usine",
            "manufacturing",
            "chimie",
        ),
    },
    {
        "id": "ecommerce",
        "labelFr": "E-commerce",
        "labelEn": "E-commerce",
        "searchTerms": (
            "ecommerce",
            "e-commerce",
            "vente en ligne",
            "marketplace",
            "shop en ligne",
        ),
    },
)

ALLOWED_TARGET_MARKET_IDS = frozenset(option["id"] for option in TARGET_MARKET_OPTIONS)

_LABELS_BY_ID = {option["id"]: option["labelFr"] for option in TARGET_MARKET_OPTIONS}


def _terms_for_market(option: dict) -> tuple[str, ...]:
    terms: list[str] = [
        str(option["id"]),
        str(option["labelFr"]).lower(),
        str(option["labelEn"]).lower(),
    ]
    for raw in option.get("searchTerms", ()):
        term = str(raw).strip().lower()
        if term and term not in terms:
            terms.append(term)
    return tuple(terms)


_SEARCH_TERMS_BY_ID: dict[str, tuple[str, ...]] = {
    option["id"]: _terms_for_market(option) for option in TARGET_MARKET_OPTIONS
}


def normalize_target_markets(values: list | None) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        market_id = str(raw).strip().lower()
        if not market_id or market_id not in ALLOWED_TARGET_MARKET_IDS:
            continue
        if market_id in seen:
            continue
        seen.add(market_id)
        normalized.append(market_id)
    return normalized


def target_market_labels(market_ids: list[str] | None) -> list[str]:
    return [_LABELS_BY_ID[market_id] for market_id in normalize_target_markets(market_ids)]


def market_search_terms(market_id: str) -> tuple[str, ...]:
    return _SEARCH_TERMS_BY_ID.get(market_id, ())


def _query_matches_term(query: str, term: str) -> bool:
    query_fold = fold_text(query)
    term_fold = fold_text(term)
    if len(term_fold) < 2:
        return False
    if query_fold == term_fold:
        return True
    if len(query_fold) >= 3 and term_fold in query_fold:
        return True
    if len(term_fold) >= 3 and query_fold in term_fold:
        return True
    return False


def matching_market_ids_for_query(query: str) -> list[str]:
    """Return market ids whose labels/aliases match the search query."""
    if not fold_text(query):
        return []

    matches: list[str] = []
    for option in TARGET_MARKET_OPTIONS:
        market_id = option["id"]
        for term in market_search_terms(market_id):
            if _query_matches_term(query, term):
                matches.append(market_id)
                break
    return matches
