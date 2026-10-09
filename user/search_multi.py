"""
Helpers to search across several fields or languages at once.
"""

# services/multi_search.py

"""
from django.db.models import Q, F
from ..models import CommercialListing, PackagingItem, PackagingSet, Event
import json


def multi_search(query: str, market_code: str = None, filters: dict = None) -> dict:
    """
    Core Paknomik multi-search algorithm.
    
    Args:
        query: Product name (e.g. "biscuit", "perfume")
        market_code: Optional market filter (e.g. "DZ" for Algeria)
        filters: Optional dict with food_drug, recyclability, max_price, etc.
    
    Returns:
        {
            "complete_sets": [...],   # pre-built supplier solutions
            "primary": [...],
            "secondary": [...],
            "tertiary": [...],
        }
    """
    filters = filters or {}

    # ── Step 1: Find matching commercial listings ──────────────────────────────
    # generique_names is a Postgres ArrayField — use icontains on each element
    listings_qs = CommercialListing.objects.filter(
        generique_names__icontains=query.lower()
    ).select_related('item', 'market')

    if market_code:
        listings_qs = listings_qs.filter(
            Q(market__code=market_code) | Q(market__isnull=True)
        )

    item_ids = listings_qs.values_list('item_id', flat=True).distinct()

    if not item_ids:
        # Fallback: search by item title directly
        item_ids = PackagingItem.objects.filter(
            title__icontains=query
        ).values_list('id', flat=True)

    # ── Step 2: Fetch and enrich packaging items ───────────────────────────────
    items_qs = PackagingItem.objects.filter(
        id__in=item_ids
    ).select_related(
        'supplier', 'material', 'form'
    ).prefetch_related(
        'values__attribute'
    ).order_by(
        '-supplier__rank', 'base_price'  # highest ranked supplier first
    )

    # Apply optional filters
    if filters.get('food_drug'):
        items_qs = items_qs.filter(food_and_drug=True)
    if filters.get('max_price'):
        items_qs = items_qs.filter(base_price__lte=filters['max_price'])
    if filters.get('max_moq'):
        items_qs = items_qs.filter(moq__lte=filters['max_moq'])

    # ── Step 3: Group by function (primary / secondary / tertiary) ─────────────
    grouped = {'primary': [], 'secondary': [], 'tertiary': []}
    supplier_ids_shown = []

    for item in items_qs:
        layer = item.function
        if layer not in grouped:
            continue

        tech_specs = [
            {
                'attribute': v.attribute.name,
                'value': v.num_val or v.txt_val or v.bool_val,
                'unit': v.unit
            }
            for v in item.values.all()
        ]

        grouped[layer].append({
            'id': item.id,
            'title': item.title,
            'description': item.description,
            'material': item.material.name if item.material else None,
            'form': item.form.name if item.form else None,
            'base_price': float(item.base_price) if item.base_price else None,
            'moq': item.moq,
            'quantity_unit': item.quantity_unit,
            'delay_days': item.delay,
            'recyclability': item.recyclability,
            'food_and_drug': item.food_and_drug,
            'tech_specs': tech_specs,
            'supplier': {
                'id': item.supplier.id,
                'name': item.supplier.name,
                'rank': item.supplier.rank,
                'availability': item.supplier.availability_times_to_respond,
            }
        })
        supplier_ids_shown.append(item.supplier.id)

    # ── Step 4: Check for pre-built complete sets ──────────────────────────────
    complete_sets = []
    relevant_sets = PackagingSet.objects.filter(
        items__id__in=item_ids
    ).distinct().select_related('supplier').prefetch_related('packagingsetitem_set__item')

    for ps in relevant_sets:
        set_items = ps.packagingsetitem_set.select_related('item__material', 'item__form')
        complete_sets.append({
            'set_id': ps.id,
            'supplier': {
                'id': ps.supplier.id,
                'name': ps.supplier.name,
                'rank': ps.supplier.rank,
            },
            'items': [
                {
                    'function': si.item.function,
                    'title': si.item.title,
                    'quantity': si.quantity,
                }
                for si in set_items
            ]
        })

    # ── Step 5: Log analytics events ──────────────────────────────────────────
    _log_search_event(query, market_code, filters, item_ids.count(), supplier_ids_shown)

    return {
        'query': query,
        'complete_sets': complete_sets,
        'primary': grouped['primary'],
        'secondary': grouped['secondary'],
        'tertiary': grouped['tertiary'],
        'total_items': sum(len(v) for v in grouped.values()),
    }


def _log_search_event(query, market_code, filters, results_count, supplier_ids):
    """Log search + per-supplier appearance events for analytics."""
    
    # Main search event
    Event.objects.create(json_context={
        'type': 'search',
        'query': query,
        'filters': {
            'market': market_code,
            **filters
        },
        'results_count': results_count
    })

    # Appearance event per supplier (feeds their monthly KPI report)
    for position, supplier_id in enumerate(dict.fromkeys(supplier_ids), start=1):
        Event.objects.create(json_context={
            'type': 'appearance_in_search',
            'supplier_id': supplier_id,
            'search_query': query,
            'position': position
        })
        """