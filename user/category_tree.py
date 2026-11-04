"""
Helpers to walk categories and subcategories in the right order.
"""

from user.models import Category


def root_category_for(subcategory: Category) -> Category | None:
    if not subcategory or not subcategory.parent_id:
        return None
    if subcategory.parent.parent_id:
        return subcategory.parent.parent
    return subcategory.parent


def subcategory_belongs_to_category(category: Category, subcategory: Category) -> bool:
    if not category or not subcategory:
        return True
    root = root_category_for(subcategory)
    return root is not None and root.id == category.id


def _unique_categories(rows: list[Category]) -> list[Category]:
    seen: set[int] = set()
    unique: list[Category] = []
    for row in rows:
        if row.id in seen:
            continue
        seen.add(row.id)
        unique.append(row)
    return unique


def selectable_subcategories(parent: Category | None = None):
    """Active product types for forms/filters (taxonomy v3 allow-list)."""
    from user.taxonomy_v3 import ACTIVE_FAMILY_SLUGS, ACTIVE_TYPE_SLUGS, active_types_queryset

    if parent is None:
        rows = list(
            active_types_queryset()
            .select_related("parent", "parent__parent")
            .order_by("sort_order", "name_en")
        )
        return _unique_categories(rows)

    if parent.slug not in ACTIVE_FAMILY_SLUGS:
        return []

    rows = list(
        Category.objects.filter(
            parent=parent,
            slug__in=ACTIVE_TYPE_SLUGS,
        )
        .select_related("parent", "parent__parent")
        .order_by("sort_order", "name_en")
    )
    return _unique_categories(rows)


def count_selectable_subcategories(parent: Category) -> int:
    return len(selectable_subcategories(parent))


def _flatten_selectable_rows(rows: list[Category]) -> list[Category]:
    from user.taxonomy_v3 import ACTIVE_TYPE_SLUGS

    expanded: list[Category] = []
    for row in rows:
        if row.parent and row.parent.parent_id:
            if row.slug in ACTIVE_TYPE_SLUGS:
                expanded.append(row)
            continue
        children = list(
            Category.objects.filter(parent=row, slug__in=ACTIVE_TYPE_SLUGS).order_by(
                "sort_order", "name_en"
            )
        )
        if children:
            expanded.extend(children)
        elif row.slug in ACTIVE_TYPE_SLUGS:
            expanded.append(row)
    return expanded
