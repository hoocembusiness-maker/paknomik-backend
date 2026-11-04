"""Compatibility shim — taxonomy v3 delegates to Pacdora-inspired v4.

Existing imports (``category_tree``, serializers, management commands) keep
working while ``user.taxonomy_v4`` is the source of truth.
"""

from user.taxonomy_v4 import (  # noqa: F401
    ACTIVE_FAMILY_SLUGS,
    ACTIVE_TYPE_SLUGS,
    FAMILIES,
    LEGACY_FAMILY_SLUG,
    LEGACY_TYPE_SLUG,
    SERVICE_FAMILY_SLUGS,
    SERVICE_TYPE_SLUGS,
    TAXONOMY_VERSION,
    TYPES,
    active_families_queryset,
    active_types_queryset,
    apply_taxonomy_v4,
    is_nav_family,
    is_nav_type,
    is_service_category,
)


def apply_taxonomy_v3(*, dry_run: bool = False) -> dict:
    """Apply the current Famille → Type taxonomy (v4)."""
    return apply_taxonomy_v4(dry_run=dry_run)
