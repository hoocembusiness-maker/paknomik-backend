"""Load the example taxonomy. Idempotent. Does not delete categories or products."""

from __future__ import annotations

import json
from pathlib import Path

from django.db import transaction

from user.catalog_taxonomy import SchemaError, validate_attribute_schema
from user.models import Category, ProductFamily, ProductType, TypeAlternative

SEED_PATH = Path(__file__).resolve().parent / "data" / "catalog_taxonomy_seed.json"


def _legacy(slugs: list[str]):
    for slug in slugs:
        found = Category.objects.filter(slug=slug).first()
        if found is not None:
            return found
    return None


@transaction.atomic
def seed_catalog_taxonomy(path: Path | None = None) -> dict:
    payload = json.loads((path or SEED_PATH).read_text(encoding="utf-8"))
    families = {}
    for row in payload["families"]:
        family, _created = ProductFamily.objects.update_or_create(
            slug=row["slug"],
            defaults={
                "name_fr": row["name_fr"],
                "name_en": row.get("name_en") or "",
                "sort_order": row.get("sort_order") or 0,
                "legacy_category": _legacy(row.get("legacy_slugs") or []),
            },
        )
        families[family.slug] = family

    types = {}
    for row in payload["types"]:
        schema = validate_attribute_schema(row["attributes"])
        product_type, _created = ProductType.objects.update_or_create(
            slug=row["slug"],
            defaults={
                "family": families[row["family"]],
                "name_fr": row["name_fr"],
                "name_en": row.get("name_en") or "",
                "sort_order": row.get("sort_order") or 0,
                "attribute_schema": schema,
                "legacy_category": _legacy(row.get("legacy_slugs") or []),
            },
        )
        types[product_type.slug] = product_type

    linked = 0
    for row in payload.get("alternatives") or []:
        TypeAlternative.objects.update_or_create(
            from_type=types[row["from"]],
            to_type=types[row["to"]],
            defaults={
                "level": row["level"],
                "bidirectional": bool(row.get("bidirectional", True)),
                "attribute_map": row.get("attribute_map") or {},
                "reason": row["reason"],
            },
        )
        linked += 1
    return {
        "families": len(families),
        "types": len(types),
        "alternatives": linked,
    }


__all__ = ["seed_catalog_taxonomy", "SEED_PATH", "SchemaError"]
