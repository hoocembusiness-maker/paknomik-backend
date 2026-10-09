"""
Data cleanup: remove obsolete gobelets/barquettes categories.
"""

from __future__ import annotations

from user.models import Category, Product, Supplier, TenderCall

RESTAURATION_PARENT_SLUG = "restauration-cafe"
FALLBACK_SUBCATEGORY_SLUG = "takeaway-boite-alimentaire"

SUBCATEGORY_SLUGS_TO_REMOVE = (
    "cups-plast",
    "cups-pap",
    "cup-lids",
    "barquettes-ouvertes",
)


def remove_gobelets_barquettes_subcategories(*, dry_run: bool = False) -> dict:
    parent = Category.objects.filter(
        slug=RESTAURATION_PARENT_SLUG,
        parent__isnull=True,
    ).first()
    if not parent:
        return {"removed": 0, "skipped": "restauration_parent_missing"}

    fallback = Category.objects.filter(
        slug=FALLBACK_SUBCATEGORY_SLUG,
        parent=parent,
    ).first()
    if not fallback:
        fallback = (
            Category.objects.filter(parent=parent)
            .exclude(slug__in=SUBCATEGORY_SLUGS_TO_REMOVE)
            .order_by("sort_order", "id")
            .first()
        )
    if not fallback:
        return {"removed": 0, "skipped": "fallback_subcategory_missing"}

    subcategories = list(
        Category.objects.filter(
            parent=parent,
            slug__in=SUBCATEGORY_SLUGS_TO_REMOVE,
        )
    )
    if not subcategories:
        return {"removed": 0, "skipped": "no_subcategories"}

    subcategory_ids = [row.id for row in subcategories]
    stats = {
        "removed": 0,
        "products_repointed": 0,
        "tender_calls_repointed": 0,
        "suppliers_updated": 0,
        "fallback_subcategory": fallback.slug,
    }

    if dry_run:
        stats["removed"] = len(subcategories)
        stats["products_repointed"] = Product.objects.filter(
            subcategory_id__in=subcategory_ids
        ).count()
        stats["tender_calls_repointed"] = TenderCall.objects.filter(
            subcategory_id__in=subcategory_ids
        ).count()
        stats["suppliers_updated"] = Supplier.objects.filter(
            interested_subcategories__in=subcategories
        ).distinct().count()
        return stats

    stats["products_repointed"] = Product.objects.filter(
        subcategory_id__in=subcategory_ids
    ).update(subcategory=fallback, category=parent)

    stats["tender_calls_repointed"] = TenderCall.objects.filter(
        subcategory_id__in=subcategory_ids
    ).update(subcategory=fallback, category=parent)

    for supplier in Supplier.objects.filter(
        interested_subcategories__in=subcategories
    ).distinct():
        supplier.interested_subcategories.remove(*subcategories)
        supplier.interested_subcategories.add(fallback)
        stats["suppliers_updated"] += 1

    Category.objects.filter(id__in=subcategory_ids).delete()
    stats["removed"] = len(subcategories)
    return stats
