"""
Data cleanup: merge duplicate supplier interest categories.
"""

from __future__ import annotations

# Legacy slugless parents that duplicate a v2 catalog category (by canonical slug).
DUPLICATE_TO_CANONICAL_SLUG = {
    "Étiquettes": "etiquettes",
    "Etiquettes": "etiquettes",
    "Bouteilles": "flacons",
    "Carton ondulé": "carton-et-derives",
    "Emballage pharmaceutique": "pharma",
    "Emballage flexible": "flexpak",
}


def _find_legacy_duplicate(Category, name_fr: str):
    return (
        Category.objects.filter(
            parent__isnull=True,
            name_fr=name_fr,
        )
        .filter(slug__isnull=True)
        .first()
    )


def cleanup_duplicate_interest_categories(
    *,
    Category,
    Product,
    Supplier,
    SourcingRequest=None,
    dry_run: bool = False,
) -> dict:
    """
    Merge slugless legacy L1 categories into their v2 canonical counterparts,
    then delete the legacy rows.
    """
    stats = {
        "merged": [],
        "skipped": [],
        "products_repointed": 0,
        "suppliers_updated": 0,
        "sourcing_requests_repointed": 0,
        "deleted": 0,
    }

    for legacy_name, canonical_slug in DUPLICATE_TO_CANONICAL_SLUG.items():
        duplicate = _find_legacy_duplicate(Category, legacy_name)
        if not duplicate:
            continue

        canonical = Category.objects.filter(
            slug=canonical_slug,
            parent__isnull=True,
        ).first()
        if not canonical:
            stats["skipped"].append(
                {"legacy_name": legacy_name, "reason": "canonical_missing"}
            )
            continue
        if duplicate.pk == canonical.pk:
            continue

        entry = {
            "legacy_id": duplicate.pk,
            "legacy_name": duplicate.name_fr,
            "canonical_id": canonical.pk,
            "canonical_slug": canonical_slug,
        }

        product_count = Product.objects.filter(category=duplicate).count()
        supplier_count = Supplier.objects.filter(
            interested_categories=duplicate
        ).count()
        sourcing_count = 0
        if SourcingRequest is not None:
            sourcing_count = SourcingRequest.objects.filter(
                category=duplicate
            ).count()

        entry["products"] = product_count
        entry["suppliers"] = supplier_count
        entry["sourcing_requests"] = sourcing_count
        stats["merged"].append(entry)

        if dry_run:
            stats["products_repointed"] += product_count
            stats["suppliers_updated"] += supplier_count
            stats["sourcing_requests_repointed"] += sourcing_count
            stats["deleted"] += 1
            continue

        stats["products_repointed"] += Product.objects.filter(
            category=duplicate
        ).update(category=canonical)

        if SourcingRequest is not None:
            stats["sourcing_requests_repointed"] += SourcingRequest.objects.filter(
                category=duplicate
            ).update(category=canonical)

        for supplier in Supplier.objects.filter(interested_categories=duplicate):
            supplier.interested_categories.add(canonical)
            supplier.interested_categories.remove(duplicate)
            stats["suppliers_updated"] += 1

        duplicate.delete()
        stats["deleted"] += 1

    return stats
