"""
One-time data logic to merge duplicate carton categories.
"""

from __future__ import annotations

COMPACT_SLUG = "boites-carton"
CORRUGATED_SLUG = "carton-ondule"
MERGED_SLUG = "carton-et-derives"

MERGED_NAMES = {
    "name_fr": "Carton et dérivés",
    "name_en": "Carton & Derivatives",
    "plain_description": (
        "Carton compact, rigide et ondulé : boîtes pliantes, caisses, "
        "planches, pochettes et dérivés."
    ),
}


def _copy_category_attributes(source_category, target_category, CategoryAttribute, AttributeOption):
    """Copy attribute links (and dropdown options) onto a subcategory."""
    for link in CategoryAttribute.objects.filter(category=source_category).prefetch_related(
        "options"
    ):
        new_link, created = CategoryAttribute.objects.get_or_create(
            category=target_category,
            attribute=link.attribute,
            defaults={
                "sort_order": link.sort_order,
                "is_required_for_publish": link.is_required_for_publish,
            },
        )
        if not created:
            continue
        for option in link.options.all():
            AttributeOption.objects.get_or_create(
                category_attribute=new_link,
                value_en=option.value_en,
                defaults={
                    "value_fr": option.value_fr,
                    "value_ar": option.value_ar,
                    "sort_order": option.sort_order,
                },
            )


def merge_carton_categories(
    *,
    Category,
    CategoryAttribute,
    AttributeOption,
    Product,
    Supplier,
    SourcingRequest=None,
    dry_run: bool = False,
) -> dict:
    """
    Merge the two L1 carton categories into « Carton et dérivés ».

    - Renames the compact parent and moves corrugated subcategories under it.
    - Copies corrugated parent attributes onto each corrugated subcategory
      so compact subcategories keep inheriting only compact attributes.
    """
    compact = Category.objects.filter(slug=COMPACT_SLUG, parent__isnull=True).first()
    corrugated = Category.objects.filter(slug=CORRUGATED_SLUG, parent__isnull=True).first()

    if not compact:
        compact = Category.objects.filter(slug=MERGED_SLUG, parent__isnull=True).first()
    if not compact:
        raise ValueError(
            f'Parent category "{COMPACT_SLUG}" (or merged "{MERGED_SLUG}") not found.'
        )

    stats = {
        "subcategories_moved": 0,
        "attribute_links_copied": 0,
        "products_repointed": 0,
        "suppliers_updated": 0,
        "sourcing_requests_repointed": 0,
        "corrugated_deleted": False,
    }

    if corrugated:
        ondule_subcategories = list(
            Category.objects.filter(parent=corrugated).order_by("sort_order")
        )
        for subcategory in ondule_subcategories:
            stats["subcategories_moved"] += 1
            if dry_run:
                continue
            before = CategoryAttribute.objects.filter(category=subcategory).count()
            _copy_category_attributes(
                corrugated, subcategory, CategoryAttribute, AttributeOption
            )
            after = CategoryAttribute.objects.filter(category=subcategory).count()
            stats["attribute_links_copied"] += max(after - before, 0)
            subcategory.parent = compact
            subcategory.save(update_fields=["parent"])

    if not dry_run:
        compact.slug = MERGED_SLUG
        compact.name_fr = MERGED_NAMES["name_fr"]
        compact.name_en = MERGED_NAMES["name_en"]
        compact.plain_description = MERGED_NAMES["plain_description"]
        compact.save(
            update_fields=["slug", "name_fr", "name_en", "plain_description"]
        )

    if corrugated:
        if not dry_run:
            product_count = Product.objects.filter(category=corrugated).update(
                category=compact
            )
            stats["products_repointed"] = product_count

            for supplier in Supplier.objects.filter(interested_categories=corrugated):
                supplier.interested_categories.add(compact)
                supplier.interested_categories.remove(corrugated)
                stats["suppliers_updated"] += 1

            if SourcingRequest is not None:
                stats["sourcing_requests_repointed"] = SourcingRequest.objects.filter(
                    category=corrugated
                ).update(category=compact)

            corrugated.delete()
            stats["corrugated_deleted"] = True

    return stats
