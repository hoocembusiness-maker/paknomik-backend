"""
Idempotent seed for Bobines categories and technical attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from user.catalog_bobines import (
    ATTRIBUTES_BY_SUBCATEGORY_SLUG,
    PARENT_CATEGORY,
    PARENT_SLUG,
    SUBCATEGORIES,
)
from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    TechnicalAttribute,
    Unit,
)


@dataclass
class BobinesSeedStats:
    parent_created: bool = False
    subcategories: int = 0
    attributes: int = 0
    links: int = 0
    options: int = 0
    errors: list[str] = field(default_factory=list)


def _resolve_unit(symbol: str | None) -> Unit | None:
    if not symbol:
        return None
    unit, _ = Unit.objects.get_or_create(
        name=symbol,
        defaults={"symbol": symbol},
    )
    return unit


def _seed_attribute_specs(subcategory: Category, specs: list[dict], stats: BobinesSeedStats):
    for spec in specs:
        stats.attributes += 1
        stats.links += 1
        unit = _resolve_unit(spec.get("unit_symbol"))
        attribute, _ = TechnicalAttribute.objects.update_or_create(
            external_id=spec["external_id"],
            defaults={
                "name_en": spec["name_en"],
                "name_fr": spec["name_fr"],
                "attribute_level": spec["attribute_level"],
                "attribute_type": spec["attribute_type"],
                "unit": unit,
                "is_required": spec.get("is_required", False),
                "is_searchable": spec.get("is_searchable", False),
                "is_comparable": spec.get("is_comparable", True),
            },
        )
        link, _ = CategoryAttribute.objects.update_or_create(
            category=subcategory,
            attribute=attribute,
            defaults={
                "sort_order": spec["sort_order"],
                "is_required_for_publish": spec.get("is_required", False),
            },
        )
        for opt_idx, (value_en, value_fr) in enumerate(spec.get("options", []), start=1):
            stats.options += 1
            AttributeOption.objects.update_or_create(
                category_attribute=link,
                value_en=value_en,
                defaults={
                    "value_fr": value_fr,
                    "sort_order": opt_idx,
                },
            )


def apply_bobines_catalog(*, dry_run: bool = False) -> BobinesSeedStats:
    """Create or update Bobines parent, subcategories, and attribute links."""
    stats = BobinesSeedStats()

    with transaction.atomic():
        parent_defaults = {**PARENT_CATEGORY, "parent": None}
        parent, created = Category.objects.update_or_create(
            external_id=PARENT_CATEGORY["external_id"],
            defaults=parent_defaults,
        )
        stats.parent_created = created

        for row in SUBCATEGORIES:
            stats.subcategories += 1
            sub_defaults = {
                **row,
                "parent": parent,
                "level": 2,
                "is_new_in_v2": True,
            }
            if dry_run:
                specs = ATTRIBUTES_BY_SUBCATEGORY_SLUG.get(row["slug"], [])
                stats.attributes += len(specs)
                stats.links += len(specs)
                for spec in specs:
                    stats.options += len(spec.get("options", []))
                continue

            subcategory, _ = Category.objects.update_or_create(
                external_id=row["external_id"],
                defaults=sub_defaults,
            )
            specs = ATTRIBUTES_BY_SUBCATEGORY_SLUG.get(row["slug"], [])
            _seed_attribute_specs(subcategory, specs, stats)

        if dry_run:
            transaction.set_rollback(True)

    return stats
