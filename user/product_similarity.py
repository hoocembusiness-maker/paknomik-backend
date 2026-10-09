"""
Finds products that look like another product (compare feature).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

from user.models import Category, CategoryAttribute, Product, ProductAttributeValue

NAME_WEIGHT = 2.0
NAME_AR_WEIGHT = 1.5
DESCRIPTION_WEIGHT = 1.5
KEYWORDS_WEIGHT = 1.0


def _fold_text(value: str) -> str:
    """Lowercase and strip accents for text comparison."""
    text = value.strip().lower()
    normalized = unicodedata.normalize('NFKD', text)
    return ''.join(char for char in normalized if not unicodedata.combining(char))


def _score_text(left: str, right: str) -> float:
    left_fold = _fold_text(left)
    right_fold = _fold_text(right)
    if left_fold == right_fold:
        return 1.0
    return SequenceMatcher(None, left_fold, right_fold).ratio()


def _optional_text_score(left: str | None, right: str | None) -> float | None:
    left_text = (left or '').strip()
    right_text = (right or '').strip()
    if not left_text or not right_text:
        return None
    return _score_text(left_text, right_text)


def _product_keyword_tokens(product: Product) -> set[str]:
    tokens: set[str] = set()
    for raw in product.custom_keywords or []:
        folded = _fold_text(str(raw))
        if folded:
            tokens.add(folded)
    cache = getattr(product, '_prefetched_objects_cache', {})
    if 'search_tags' in cache:
        tags = product.search_tags.all()
    else:
        tags = product.search_tags.all()
    for tag in tags:
        for value in (tag.tag, tag.tag_fr, tag.tag_ar):
            folded = _fold_text(value or '')
            if folded:
                tokens.add(folded)
    return tokens


def _keyword_similarity(left: Product, right: Product) -> float | None:
    left_tokens = _product_keyword_tokens(left)
    right_tokens = _product_keyword_tokens(right)
    if not left_tokens or not right_tokens:
        return None
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(intersection) / len(union)


def _attribute_category(product: Product) -> Category | None:
    """Category node that defines technical attributes for a product."""
    subcategory = product.subcategory
    category = product.category
    if subcategory and CategoryAttribute.objects.filter(category=subcategory).exists():
        return subcategory
    if category and CategoryAttribute.objects.filter(category=category).exists():
        return category
    if category and category.parent_id:
        return category.parent
    return category


def _attribute_values_map(product: Product) -> dict[int, ProductAttributeValue]:
    cache = getattr(product, '_prefetched_objects_cache', {})
    if 'attribute_values' in cache:
        values = product.attribute_values.all()
    else:
        values = product.attribute_values.select_related('attribute').all()
    return {av.attribute_id: av for av in values}


def _has_meaningful_value(av: ProductAttributeValue) -> bool:
    attribute = av.attribute
    if attribute.attribute_type == 'number':
        return av.num_value is not None
    if attribute.attribute_type == 'boolean':
        return True
    return bool((av.txt_value or '').strip())


def _attribute_weight(av: ProductAttributeValue) -> float:
    attribute = av.attribute
    weight = 1.0
    if attribute.attribute_level == 1:
        weight += 0.5
    if attribute.is_comparable:
        weight += 0.3
    return weight


def _score_number(left: float, right: float, tolerance: float) -> float:
    if left == right:
        return 1.0
    scale = max(abs(left), abs(right), 1e-9)
    relative_diff = abs(left - right) / scale
    if relative_diff <= tolerance:
        return 1.0
    return max(0.0, 1.0 - relative_diff)


def _score_attribute_pair(
    left: ProductAttributeValue,
    right: ProductAttributeValue,
    *,
    number_tolerance: float,
) -> float:
    attribute_type = left.attribute.attribute_type
    if attribute_type == 'number':
        if left.num_value is None or right.num_value is None:
            return 0.0
        return _score_number(left.num_value, right.num_value, number_tolerance)
    if attribute_type == 'boolean':
        return 1.0 if left.bool_value == right.bool_value else 0.0
    left_text = (left.txt_value or '').strip()
    right_text = (right.txt_value or '').strip()
    if not left_text or not right_text:
        return 0.0
    return _score_text(left_text, right_text)


@dataclass
class AttributeSimilarity:
    attribute_id: int
    attribute_name: str
    score: float
    weight: float


@dataclass
class ProductSimilarityResult:
    score: float
    comparable: bool
    reason: str = ''
    shared_attributes: int = 0
    attribute_details: list[AttributeSimilarity] = field(default_factory=list)


@dataclass
class RankedSimilarProduct:
    product: Product
    result: ProductSimilarityResult


class ProductSimilarityChecker:
    """Assess how similar two products are within the same subcategory."""

    def __init__(
        self,
        *,
        number_tolerance: float = 0.05,
        min_shared_attributes: int = 0,
    ):
        self.number_tolerance = number_tolerance
        self.min_shared_attributes = min_shared_attributes

    def same_subcategory(self, left: Product, right: Product) -> bool:
        if not left.subcategory_id or not right.subcategory_id:
            return False
        return left.subcategory_id == right.subcategory_id

    def compare(self, left: Product, right: Product) -> ProductSimilarityResult:
        if left.pk and right.pk and left.pk == right.pk:
            return ProductSimilarityResult(
                score=1.0,
                comparable=True,
                reason='Same product',
                shared_attributes=0,
            )

        if not self.same_subcategory(left, right):
            return ProductSimilarityResult(
                score=0.0,
                comparable=False,
                reason='Products must share the same subcategory.',
            )

        weighted_signals: list[tuple[float, float]] = []

        name_score = _optional_text_score(left.name, right.name)
        if name_score is not None:
            weighted_signals.append((name_score, NAME_WEIGHT))

        name_ar_score = _optional_text_score(left.name_ar, right.name_ar)
        if name_ar_score is not None:
            weighted_signals.append((name_ar_score, NAME_AR_WEIGHT))

        description_score = _optional_text_score(left.description, right.description)
        if description_score is not None:
            weighted_signals.append((description_score, DESCRIPTION_WEIGHT))

        keyword_score = _keyword_similarity(left, right)
        if keyword_score is not None:
            weighted_signals.append((keyword_score, KEYWORDS_WEIGHT))

        left_values = _attribute_values_map(left)
        right_values = _attribute_values_map(right)
        attribute_scope = _attribute_category(left)
        details: list[AttributeSimilarity] = []

        if attribute_scope is not None:
            scoped_attribute_ids = set(
                CategoryAttribute.objects.filter(category=attribute_scope).values_list(
                    'attribute_id', flat=True
                )
            )
            shared_ids = scoped_attribute_ids & set(left_values) & set(right_values)

            for attribute_id in sorted(shared_ids):
                left_av = left_values[attribute_id]
                right_av = right_values[attribute_id]
                if not _has_meaningful_value(left_av) or not _has_meaningful_value(right_av):
                    continue

                attr_score = _score_attribute_pair(
                    left_av,
                    right_av,
                    number_tolerance=self.number_tolerance,
                )
                weight = (_attribute_weight(left_av) + _attribute_weight(right_av)) / 2
                details.append(
                    AttributeSimilarity(
                        attribute_id=attribute_id,
                        attribute_name=left_av.attribute.name_en,
                        score=attr_score,
                        weight=weight,
                    )
                )
                weighted_signals.append((attr_score, weight))

        if len(details) < self.min_shared_attributes and self.min_shared_attributes > 0:
            attribute_signals = [
                (score, weight)
                for score, weight in weighted_signals
                if weight not in (NAME_WEIGHT, NAME_AR_WEIGHT, DESCRIPTION_WEIGHT, KEYWORDS_WEIGHT)
            ]
            if not attribute_signals:
                return ProductSimilarityResult(
                    score=0.0,
                    comparable=True,
                    reason=(
                        f'Need at least {self.min_shared_attributes} shared technical '
                        'attribute(s) with values on both products.'
                    ),
                    shared_attributes=len(details),
                    attribute_details=details,
                )

        if not weighted_signals:
            return ProductSimilarityResult(
                score=0.0,
                comparable=True,
                reason='No comparable name, description, keywords, or attributes.',
                shared_attributes=len(details),
                attribute_details=details,
            )

        total_weight = sum(weight for _, weight in weighted_signals)
        score = sum(value * weight for value, weight in weighted_signals) / total_weight
        return ProductSimilarityResult(
            score=score,
            comparable=True,
            shared_attributes=len(details),
            attribute_details=details,
        )

    def similarity(self, left: Product, right: Product) -> float:
        return self.compare(left, right).score

    def find_similar(
        self,
        product: Product,
        candidates: Iterable[Product] | None = None,
        *,
        limit: int = 10,
        min_score: float = 0.0,
        active_only: bool = True,
    ) -> list[RankedSimilarProduct]:
        """Return candidate products ordered by combined similarity."""
        if not product.subcategory_id:
            return []

        if candidates is None:
            queryset = Product.objects.filter(subcategory_id=product.subcategory_id)
            if active_only:
                queryset = queryset.filter(is_active=True)
            if product.pk:
                queryset = queryset.exclude(pk=product.pk)
            candidates = queryset.prefetch_related(
                'attribute_values__attribute',
                'search_tags',
            )
        else:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.subcategory_id == product.subcategory_id
                and candidate.pk != product.pk
            ]

        ranked: list[RankedSimilarProduct] = []
        for candidate in candidates:
            result = self.compare(product, candidate)
            if not result.comparable or result.score < min_score:
                continue
            ranked.append(RankedSimilarProduct(product=candidate, result=result))

        ranked.sort(
            key=lambda item: (
                -item.result.score,
                -item.result.shared_attributes,
                item.product.name.lower(),
            )
        )
        return ranked[:limit]


def find_similar_products(
    product: Product,
    *,
    limit: int = 5,
    active_only: bool = True,
) -> list[Product]:
    """Return active products in the same subcategory, best match first."""
    if not product.subcategory_id:
        return []

    checker = ProductSimilarityChecker()
    ranked = checker.find_similar(
        product,
        limit=limit,
        min_score=0.0,
        active_only=active_only,
    )
    if ranked:
        return [item.product for item in ranked]

    queryset = Product.objects.filter(
        subcategory_id=product.subcategory_id,
        is_active=True,
    ).exclude(pk=product.pk)
    return list(
        queryset.select_related("supplier", "category", "subcategory", "material")
        .prefetch_related("images", "search_tags", "attribute_values__attribute")
        .order_by("-is_featured", "-created_at")[:limit]
    )
