"""
Helpers to attach and validate search tags on products.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.db.models import Q

from user.models import SearchTag

SUPPLIER_SEARCH_TAG_WEIGHT = 1


def normalize_supplier_keyword(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    word = " ".join(raw.strip().split())
    if not word:
        return None
    return word


def find_search_tag_for_keyword(keyword: str) -> SearchTag | None:
    return SearchTag.objects.filter(
        Q(tag__iexact=keyword)
        | Q(tag_fr__iexact=keyword)
        | Q(tag_ar__iexact=keyword)
    ).first()


def get_or_create_supplier_search_tag(keyword: str) -> SearchTag:
    """Persist a supplier keyword in SearchTag for reuse across suppliers."""
    existing = find_search_tag_for_keyword(keyword)
    if existing:
        return existing

    try:
        return SearchTag.objects.create(
            tag=keyword,
            tag_fr=keyword,
            search_weight=SUPPLIER_SEARCH_TAG_WEIGHT,
        )
    except IntegrityError:
        existing = find_search_tag_for_keyword(keyword)
        if existing:
            return existing
        raise


def resolve_search_tag_ids(
    search_tag_ids: list[int] | None,
    custom_keywords: list[str] | None,
) -> list[int]:
    """Merge catalog tag ids with supplier keywords promoted to SearchTag rows."""
    unique_ids = list(dict.fromkeys(search_tag_ids or []))
    seen_keywords: set[str] = set()

    for raw in custom_keywords or []:
        keyword = normalize_supplier_keyword(raw)
        if not keyword:
            continue
        key = keyword.casefold()
        if key in seen_keywords:
            continue
        seen_keywords.add(key)
        tag = get_or_create_supplier_search_tag(keyword)
        if tag.id not in unique_ids:
            unique_ids.append(tag.id)

    return unique_ids


def apply_product_search_tags(product, tag_ids: list[int]) -> None:
    unique_ids = list(dict.fromkeys(tag_ids or []))
    tags = list(SearchTag.objects.filter(id__in=unique_ids))
    if len(tags) != len(unique_ids):
        from rest_framework import serializers

        raise serializers.ValidationError(
            {"searchTagIds": "Un ou plusieurs mots-clés sont invalides."}
        )
    tag_by_id = {tag.id: tag for tag in tags}
    product.search_tags.set([tag_by_id[tag_id] for tag_id in unique_ids])


def promote_product_custom_keywords(product) -> list[int]:
    """Move legacy JSON custom_keywords onto search_tags and clear the field."""
    tag_ids = list(product.search_tags.values_list("id", flat=True))
    legacy = product.custom_keywords or []
    if not legacy:
        return tag_ids

    resolved = resolve_search_tag_ids(tag_ids, legacy)
    apply_product_search_tags(product, resolved)
    if product.custom_keywords:
        product.custom_keywords = []
        product.save(update_fields=["custom_keywords"])
    return resolved
