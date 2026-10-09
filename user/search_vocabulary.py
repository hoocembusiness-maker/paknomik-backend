"""
Lexique technique des termes de recherche Paknomik → sous-catégories catalogue.

Alimente :
- la détection d'intention acheteur (signaux CRM « category_intent ») ;
- l'API ``/api/search/vocabulary/`` (descriptif machine-readable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from django.db.models import Q

from user.models import Category, Product, SearchTag
from user.search_engine import expand_query_tokens, normalize_phrase, tokenize_query
from user.search_normalize import fold_text

_TOKEN_SPLIT = re.compile(r"[\s/|,;·]+")


@dataclass(frozen=True)
class VocabularyTerm:
    term: str
    normalized: str
    subcategory_id: int
    subcategory_label: str
    category_label: str
    source: str  # taxonomy | search_tag | product_tag | synonym


def _label(category: Category | None) -> str:
    if category is None:
        return ""
    return (category.name_fr or category.name_en or category.slug or "").strip()


def _register_term(
    bucket: dict[str, set[int]],
    raw: str,
    subcategory_id: int,
) -> None:
    text = (raw or "").strip()
    if not text or len(text) < 2:
        return
    key = fold_text(normalize_phrase(text))
    if not key:
        return
    bucket.setdefault(key, set()).add(subcategory_id)
    for piece in _TOKEN_SPLIT.split(text):
        piece = piece.strip()
        if len(piece) >= 3:
            folded = fold_text(normalize_phrase(piece))
            if folded:
                bucket.setdefault(folded, set()).add(subcategory_id)


def _match_subcategory_ids_for_free_label(label: str) -> set[int]:
    """Map a free-text label (SearchTag.sub_category, industry…) to catalog subcategories."""
    needle = fold_text(normalize_phrase(label))
    if not needle or len(needle) < 3:
        return set()
    matched: set[int] = set()
    subs = Category.objects.filter(parent__isnull=False).select_related("parent")
    for sub in subs:
        for field in (sub.name_en, sub.name_fr, sub.name_ar, sub.slug or ""):
            folded = fold_text(normalize_phrase(field))
            if not folded:
                continue
            if needle == folded or needle in folded or folded in needle:
                matched.add(sub.pk)
                break
    return matched


@lru_cache(maxsize=1)
def _term_to_subcategory_ids() -> dict[str, set[int]]:
    bucket: dict[str, set[int]] = {}

    subs = list(
        Category.objects.filter(parent__isnull=False).select_related("parent")
    )
    for sub in subs:
        for field in (
            sub.name_en,
            sub.name_fr,
            sub.name_ar,
            sub.slug or "",
            sub.plain_description or "",
        ):
            _register_term(bucket, field, sub.pk)

    tag_sub_ids: dict[int, set[int]] = {}
    product_rows = (
        Product.objects.filter(is_active=True, subcategory_id__isnull=False)
        .values_list("search_tags__id", "subcategory_id")
        .distinct()
    )
    for tag_id, sub_id in product_rows:
        if tag_id and sub_id:
            tag_sub_ids.setdefault(tag_id, set()).add(sub_id)

    for tag in SearchTag.objects.all().only(
        "id", "tag", "tag_fr", "tag_ar", "synonyms", "sub_category"
    ):
        linked = set(tag_sub_ids.get(tag.id, set()))
        linked |= _match_subcategory_ids_for_free_label(tag.sub_category)
        if not linked:
            continue
        for sub_id in linked:
            for field in (tag.tag, tag.tag_fr, tag.tag_ar):
                _register_term(bucket, field, sub_id)
            for syn in (tag.synonyms or "").split(","):
                _register_term(bucket, syn.strip(), sub_id)

    return bucket


def resolve_subcategories_from_query(query: str) -> list[Category]:
    """Return catalog subcategories implied by a buyer search query."""
    stripped = (query or "").strip()
    if not stripped:
        return []

    from user.search_keyword_resolver import resolve_subcategories_for_crm

    mapped = resolve_subcategories_for_crm(stripped)
    if mapped:
        return mapped

    lexicon = _term_to_subcategory_ids()
    tokens = tokenize_query(stripped)
    expanded = expand_query_tokens(tokens, for_db=False)

    candidate_keys: set[str] = set()
    phrase_key = fold_text(normalize_phrase(stripped))
    if phrase_key:
        candidate_keys.add(phrase_key)
    for token in tokens:
        candidate_keys.add(fold_text(normalize_phrase(token)))
        for variant in expanded.get(token, set()):
            candidate_keys.add(fold_text(normalize_phrase(variant)))

    matched_ids: set[int] = set()
    for key in candidate_keys:
        if not key:
            continue
        if key in lexicon:
            matched_ids |= lexicon[key]
        else:
            for term, ids in lexicon.items():
                if len(key) >= 4 and (key in term or term in key):
                    matched_ids |= ids

    if not matched_ids:
        return []

    subs = Category.objects.filter(pk__in=matched_ids, parent__isnull=False)
    return list(subs.order_by("sort_order", "name_en"))


def invalidate_vocabulary_cache() -> None:
    _term_to_subcategory_ids.cache_clear()


def build_vocabulary_descriptor(*, include_entries: bool = True) -> dict:
    """
    Technical description of known search terms (for API / documentation).
    """
    lexicon = _term_to_subcategory_ids()
    subs = {
        c.pk: c
        for c in Category.objects.filter(parent__isnull=False).select_related("parent")
    }
    tag_count = SearchTag.objects.count()
    product_tag_links = (
        Product.objects.filter(is_active=True, search_tags__isnull=False)
        .values("search_tags")
        .distinct()
        .count()
    )

    from user.models import SearchTerm, SearchTermMapping

    map_entries: list[dict] = []
    if include_entries:
        mappings = (
            SearchTermMapping.objects.filter(is_active=True, search_term__is_active=True)
            .select_related(
                "search_term",
                "category",
                "subcategory",
                "subcategory__parent",
                "attribute",
            )
            .order_by("-search_term__priority", "search_term__term")[:5000]
        )
        for row in mappings:
            map_entries.append(
                {
                    "source": "search_map",
                    "term": row.search_term.term,
                    "termNormalized": row.search_term.normalized_term,
                    "termType": row.search_term.term_type,
                    "language": row.search_term.language,
                    "isAmbiguous": row.search_term.is_ambiguous,
                    "categoryId": row.category_id,
                    "categoryLabel": _label(row.category),
                    "subcategoryId": row.subcategory_id,
                    "subcategoryLabel": _label(row.subcategory),
                    "subcategorySlug": row.subcategory.slug if row.subcategory else None,
                    "attributeId": row.attribute_id,
                    "attributeLabel": row.attribute.name_fr or row.attribute.name_en
                    if row.attribute
                    else None,
                    "attributeValue": row.attribute_value or None,
                    "confidence": row.confidence,
                    "priority": row.priority,
                    "context": row.context or None,
                }
            )

    entries: list[dict] = []
    if include_entries:
        seen: set[tuple[str, int]] = set()
        for normalized, sub_ids in sorted(lexicon.items(), key=lambda row: row[0]):
            for sub_id in sorted(sub_ids):
                dedupe = (normalized, sub_id)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                sub = subs.get(sub_id)
                if sub is None:
                    continue
                entries.append(
                    {
                        "source": "legacy_lexicon",
                        "termNormalized": normalized,
                        "subcategoryId": sub_id,
                        "subcategoryLabel": _label(sub),
                        "categoryLabel": _label(sub.parent if sub else None),
                        "subcategorySlug": sub.slug,
                    }
                )

    search_term_count = SearchTerm.objects.filter(is_active=True).count()
    mapping_count = SearchTermMapping.objects.filter(is_active=True).count()

    return {
        "version": 2,
        "description": (
            "Map hiérarchique SearchTerm → SearchTermMapping (catégorie, sous-catégorie, "
            "attribut) plus lexique legacy (taxonomy, SearchTag, produits). "
            "Les termes ambigus peuvent pointer vers plusieurs sous-catégories."
        ),
        "sources": [
            {
                "id": "search_map",
                "label": "SearchTerm + SearchTermMapping (administrable, pondéré)",
            },
            {
                "id": "taxonomy",
                "label": "Arbre catégories / sous-catégories (name_en, name_fr, name_ar, slug)",
            },
            {
                "id": "search_tag",
                "label": "Modèle SearchTag (tag, tag_fr, tag_ar, synonyms, sub_category)",
            },
            {
                "id": "product_tag",
                "label": "Tags associés à des produits actifs (subcategory du produit)",
            },
            {
                "id": "query_expansion",
                "label": "Variantes token (search_engine.expand_query_tokens, packaging_taxonomy)",
            },
        ],
        "stats": {
            "subcategoryCount": len(subs),
            "normalizedTermCount": len(lexicon),
            "searchTermCount": search_term_count,
            "searchMappingCount": mapping_count,
            "entryCount": len(entries) if include_entries else None,
            "mapEntryCount": len(map_entries) if include_entries else None,
            "searchTagRows": tag_count,
            "activeProductTagLinks": product_tag_links,
            "csvReference": "user/paknomik_mots_cles_produits_exhaustif.csv",
            "importCommand": "python manage.py import_search_tags_exhaustif",
            "seedCommand": "migration 0069 / user.search_map_seed.seed_bobines_search_map",
        },
        "crmSignalRule": (
            "Un acheteur connecté déclenche un signal category_intent si sa recherche "
            "(mot-clé q et/ou filtres category + subcategory_id) résout au moins une "
            "sous-catégorie et le fournisseur a un produit actif ou la sous-catégorie "
            "dans ses centres d'intérêt."
        ),
        "mapEntries": map_entries if include_entries else None,
        "entries": entries if include_entries else None,
    }
