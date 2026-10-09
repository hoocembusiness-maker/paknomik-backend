"""
Autocomplete suggestions while typing in the search bar.
"""

from __future__ import annotations

from user.models import Product, Supplier
from user.product_search import filter_products_by_text
from user.models import Category
from user.search_keyword_resolver import resolve_search_map
from user.search_similarity import get_term_suggestions
from user.supplier_search import filter_suppliers_by_text


def build_search_suggestions(query: str, limit: int = 5) -> dict:
    query = query.strip()
    if len(query) < 2:
        return {
            'results': [],
            'prefixTerms': [],
            'similarTerms': [],
            'didYouMean': None,
        }

    products = filter_products_by_text(
        Product.objects.filter(is_active=True).select_related(
            'category', 'subcategory'
        ),
        query,
    )[:limit]
    suppliers = filter_suppliers_by_text(
        Supplier.objects.filter(is_active=True),
        query,
    )[:limit]

    results = [
        {
            'type': 'product',
            'id': product.id,
            'name': product.name,
            'url': f'/product/{product.id}/',
        }
        for product in products
    ]
    results.extend([
        {
            'type': 'supplier',
            'id': supplier.id,
            'name': supplier.company_name,
            'url': f'/supplier/{supplier.id}/',
        }
        for supplier in suppliers
    ])

    term_data = get_term_suggestions(query, limit=limit)
    has_results = bool(results)

    did_you_mean = term_data['didYouMean']
    if has_results:
        did_you_mean = None

    map_hints: list[dict] = []
    seen_sub: set[int] = set()
    for hit in resolve_search_map(query, limit=8):
        if not hit.subcategory_id or hit.subcategory_id in seen_sub:
            continue
        seen_sub.add(hit.subcategory_id)
        sub = Category.objects.filter(pk=hit.subcategory_id).select_related("parent").first()
        if sub is None:
            continue
        map_hints.append(
            {
                "type": "subcategory",
                "id": sub.id,
                "label": sub.name_fr or sub.name_en,
                "categoryLabel": (sub.parent.name_fr or sub.parent.name_en)
                if sub.parent
                else "",
                "term": hit.term,
                "score": round(hit.score, 1),
                "ambiguous": hit.is_ambiguous_term,
            }
        )
        if len(map_hints) >= limit:
            break

    return {
        'results': results,
        'prefixTerms': term_data['prefixTerms'],
        'similarTerms': term_data['similarTerms'],
        'didYouMean': did_you_mean,
        'mapHints': map_hints,
    }
