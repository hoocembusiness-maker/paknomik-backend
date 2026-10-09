"""
Scores how complete a product listing is (missing fields).
"""

from __future__ import annotations

from user.models import CategoryAttribute, Product, ProductAttributeValue
from user.product_markets import normalize_target_markets

WEIGHT_NAME = 10
WEIGHT_DESCRIPTION = 15
WEIGHT_SUBCATEGORY = 10
WEIGHT_PRICE = 10
WEIGHT_IMAGES = 20
WEIGHT_KEYWORDS = 10
WEIGHT_TECHNICAL = 25

PRODUCT_ADD_BASE_POINTS = 25


def _attribute_category(product: Product):
    subcategory = product.subcategory
    category = product.category
    if subcategory and CategoryAttribute.objects.filter(category=subcategory).exists():
        return subcategory
    if CategoryAttribute.objects.filter(category=category).exists():
        return category
    if category.parent_id:
        return category.parent
    return category


def _attribute_value_filled(value: ProductAttributeValue) -> bool:
    attribute = value.attribute
    if attribute.attribute_type == "boolean":
        return value.bool_value
    if attribute.attribute_type == "number":
        return value.num_value is not None
    return bool((value.txt_value or "").strip())


def _technical_fill_ratio(product: Product) -> float:
    if not product.subcategory_id:
        return 1.0

    category = _attribute_category(product)
    attribute_ids = list(
        CategoryAttribute.objects.filter(category=category).values_list(
            "attribute_id", flat=True
        )
    )
    if not attribute_ids:
        return 1.0

    values = {
        row.attribute_id: row
        for row in product.attribute_values.filter(attribute_id__in=attribute_ids)
    }
    filled = sum(
        1
        for attribute_id in attribute_ids
        if attribute_id in values and _attribute_value_filled(values[attribute_id])
    )
    return filled / len(attribute_ids)


def _description_filled(product: Product) -> bool:
    description = (product.description or "").strip()
    name = (product.name or "").strip()
    return bool(description) and description != name and len(description) >= 10


def calculate_product_completeness(product: Product) -> int:
    """Return completeness percentage for a saved product (0–100)."""
    score = 0.0

    if (product.name or "").strip():
        score += WEIGHT_NAME

    if _description_filled(product):
        score += WEIGHT_DESCRIPTION

    if product.subcategory_id:
        score += WEIGHT_SUBCATEGORY

    if product.price is not None:
        score += WEIGHT_PRICE

    if product.images.exists():
        score += WEIGHT_IMAGES

    has_keywords = (
        product.search_tags.exists()
        or bool(product.custom_keywords)
        or bool(normalize_target_markets(product.target_markets))
    )
    if has_keywords:
        score += WEIGHT_KEYWORDS

    score += WEIGHT_TECHNICAL * _technical_fill_ratio(product)

    return max(0, min(100, round(score)))


def catalog_points_for_product_add(completeness_pct: int) -> int:
    """Paknomik catalog points for a new product (base 25 × completeness)."""
    pct = max(0, min(100, completeness_pct))
    return round(PRODUCT_ADD_BASE_POINTS * pct / 100)
