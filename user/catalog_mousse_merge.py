"""
Data cleanup: move mousse under industrial packaging.
"""

from __future__ import annotations

from user.models import (
    Category,
    Product,
    SourcingRequest,
    Supplier,
    TenderCall,
)

MOUSSE_SLUG = "mousse"
EMBALLAGE_INDUS_SLUG = "emballage-indus"
MOUSSE_GROUP_SORT_ORDER = 1066


def move_mousse_under_emballage_industriel(*, dry_run: bool = False) -> dict:
    emballage = Category.objects.filter(
        slug=EMBALLAGE_INDUS_SLUG,
        parent__isnull=True,
    ).first()
    mousse = Category.objects.filter(
        slug=MOUSSE_SLUG,
        parent__isnull=True,
    ).first()

    if not emballage:
        return {"moved": False, "skipped": "emballage_parent_missing"}
    if not mousse:
        return {"moved": False, "skipped": "mousse_parent_missing"}

    foam_subcategories = list(
        Category.objects.filter(parent=mousse).order_by("sort_order", "name_en")
    )

    stats = {
        "moved": True,
        "foam_subcategories": len(foam_subcategories),
        "products_updated": Product.objects.filter(category=mousse).count(),
        "tender_calls_updated": TenderCall.objects.filter(category=mousse).count(),
        "sourcing_requests_updated": SourcingRequest.objects.filter(
            category=mousse
        ).count(),
        "suppliers_updated": Supplier.objects.filter(
            interested_categories=mousse
        ).count(),
    }

    if dry_run:
        return stats

    mousse.parent = emballage
    mousse.level = 2
    mousse.sort_order = MOUSSE_GROUP_SORT_ORDER
    mousse.save(update_fields=["parent", "level", "sort_order"])

    for subcategory in foam_subcategories:
        subcategory.level = 3
        subcategory.save(update_fields=["level"])

    Product.objects.filter(category=mousse).update(category=emballage)
    TenderCall.objects.filter(category=mousse).update(category=emballage)
    SourcingRequest.objects.filter(category=mousse).update(category=emballage)

    for supplier in Supplier.objects.filter(interested_categories=mousse):
        supplier.interested_categories.remove(mousse)
        supplier.interested_categories.add(emballage)

    return stats
