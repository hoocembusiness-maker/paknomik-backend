"""
Data cleanup: move restauration packaging categories.
"""

from __future__ import annotations

from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    Product,
    SourcingRequest,
    TenderCall,
)

OLD_PARENT_SLUG = "barquettes-gobelets"
NEW_PARENT_SLUG = "restauration-cafe"

SUBCATEGORY_SLUGS = (
    "cups-plast",
    "cups-pap",
    "cup-lids",
    "barquettes-ouvertes",
)

SUBCATEGORY_SORT_ORDERS = {
    "cups-plast": 1404,
    "cups-pap": 1405,
    "cup-lids": 1406,
    "barquettes-ouvertes": 1407,
}


def _copy_category_attributes(source: Category, target: Category) -> int:
    copied = 0
    for link in CategoryAttribute.objects.filter(category=source).prefetch_related(
        "options"
    ):
        target_link, created = CategoryAttribute.objects.get_or_create(
            category=target,
            attribute=link.attribute,
            defaults={
                "sort_order": link.sort_order,
                "is_required_for_publish": link.is_required_for_publish,
            },
        )
        if not created:
            continue
        copied += 1
        for option in link.options.all():
            AttributeOption.objects.get_or_create(
                category_attribute=target_link,
                value_en=option.value_en,
                defaults={
                    "value_fr": option.value_fr,
                    "value_ar": option.value_ar,
                    "sort_order": option.sort_order,
                },
            )
    return copied


def move_barquettes_gobelets_under_restauration(*, dry_run: bool = False) -> dict:
    old_parent = Category.objects.filter(
        slug=OLD_PARENT_SLUG,
        parent__isnull=True,
    ).first()
    new_parent = Category.objects.filter(
        slug=NEW_PARENT_SLUG,
        parent__isnull=True,
    ).first()

    if not old_parent:
        return {"moved": 0, "skipped": "old_parent_missing"}
    if not new_parent:
        return {"moved": 0, "skipped": "new_parent_missing"}

    subcategories = list(
        Category.objects.filter(parent=old_parent, slug__in=SUBCATEGORY_SLUGS)
    )
    if not subcategories:
        return {"moved": 0, "skipped": "no_subcategories"}

    stats = {
        "moved": 0,
        "attributes_copied": 0,
        "products_updated": 0,
        "tender_calls_updated": 0,
        "sourcing_requests_updated": 0,
        "old_parent_removed": False,
    }

    if dry_run:
        stats["moved"] = len(subcategories)
        stats["products_updated"] = Product.objects.filter(category=old_parent).count()
        stats["tender_calls_updated"] = TenderCall.objects.filter(
            category=old_parent
        ).count()
        stats["sourcing_requests_updated"] = SourcingRequest.objects.filter(
            category=old_parent
        ).count()
        stats["attributes_copied"] = (
            CategoryAttribute.objects.filter(category=old_parent).count()
            * len(subcategories)
        )
        return stats

    for subcategory in subcategories:
        subcategory.parent = new_parent
        subcategory.sort_order = SUBCATEGORY_SORT_ORDERS.get(
            subcategory.slug or "",
            subcategory.sort_order,
        )
        subcategory.save(update_fields=["parent", "sort_order"])
        stats["attributes_copied"] += _copy_category_attributes(old_parent, subcategory)
        stats["moved"] += 1

    stats["products_updated"] = Product.objects.filter(category=old_parent).update(
        category=new_parent
    )
    stats["tender_calls_updated"] = TenderCall.objects.filter(
        category=old_parent
    ).update(category=new_parent)
    stats["sourcing_requests_updated"] = SourcingRequest.objects.filter(
        category=old_parent
    ).update(category=new_parent)

    if not Category.objects.filter(parent=old_parent).exists():
        old_parent.delete()
        stats["old_parent_removed"] = True

    return stats
