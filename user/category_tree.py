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
    """Leaf subcategories for forms/filters; expands groups such as Promotion."""
    if parent is None:
        # Only start from nodes directly under a root. Including deeper leaves
        # here would list them twice (once via their group, once as themselves).
        qs = Category.objects.filter(
            parent__isnull=False,
            parent__parent__isnull=True,
        ).select_related("parent", "parent__parent")
    else:
        qs = Category.objects.filter(parent=parent).select_related("parent", "parent__parent")

    rows = list(qs.order_by("sort_order", "name_en"))
    if parent is None:
        return _unique_categories(_flatten_selectable_rows(rows))

    expanded: list[Category] = []
    for row in rows:
        children = list(
            Category.objects.filter(parent=row)
            .select_related("parent", "parent__parent")
            .order_by("sort_order", "name_en")
        )
        if children:
            expanded.extend(children)
        else:
            expanded.append(row)
    return _unique_categories(expanded)


def count_selectable_subcategories(parent: Category) -> int:
    return len(selectable_subcategories(parent))


def _flatten_selectable_rows(rows: list[Category]) -> list[Category]:
    expanded: list[Category] = []
    for row in rows:
        if row.parent and row.parent.parent_id:
            expanded.append(row)
            continue
        children = list(
            Category.objects.filter(parent=row).order_by("sort_order", "name_en")
        )
        if children:
            expanded.extend(children)
        else:
            expanded.append(row)
    return expanded
