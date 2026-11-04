"""
Direct competitors on Paknomik.

Rule: two active suppliers are competitors when they each have at least one
active product in the same subcategory (Category used as product.subcategory).
"""

from __future__ import annotations

from django.db.models import QuerySet

from user.models import Supplier


def active_subcategory_ids(supplier) -> list[int]:
    return list(
        supplier.products.filter(is_active=True, subcategory_id__isnull=False)
        .values_list("subcategory_id", flat=True)
        .distinct()
    )


def competitor_supplier_queryset(supplier) -> QuerySet[Supplier]:
    subcategory_ids = active_subcategory_ids(supplier)
    if not subcategory_ids:
        return Supplier.objects.none()
    return (
        Supplier.objects.filter(is_active=True, is_ghost=False)
        .filter(
            products__is_active=True,
            products__subcategory_id__in=subcategory_ids,
        )
        .exclude(pk=supplier.pk)
        .distinct()
    )


def competitor_supplier_ids(supplier) -> list[int]:
    return list(competitor_supplier_queryset(supplier).values_list("id", flat=True))


def count_direct_competitors(supplier, *, cap: int | None = None) -> int:
    total = competitor_supplier_queryset(supplier).count()
    if cap is not None:
        return min(cap, total)
    return total


def suppliers_share_subcategory(supplier_a, supplier_b) -> bool:
    if supplier_a.pk == supplier_b.pk:
        return False
    subs_a = set(active_subcategory_ids(supplier_a))
    if not subs_a:
        return False
    subs_b = set(active_subcategory_ids(supplier_b))
    return bool(subs_a.intersection(subs_b))
