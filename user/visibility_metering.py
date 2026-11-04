"""
Monthly visibility consumption (plan quotas / Pragmadinar catalog meters).

Hooked from analytics events so every active supplier accrues usage, including
during the all-suppliers Business promo (always_track).
"""

from __future__ import annotations

import logging

from user import entitlements
from user.events import is_supplier_self_view
from user.models import Supplier
from user.platform_config import get_bool

logger = logging.getLogger(__name__)


def metering_enabled() -> bool:
    return get_bool("visibility_metering_enabled", True)


def _meter(supplier_id: int, key: str, *, request=None, amount: int = 1) -> None:
    if not metering_enabled() or not supplier_id:
        return
    if request is not None and is_supplier_self_view(request, supplier_id):
        return
    supplier = Supplier.objects.filter(pk=supplier_id, is_active=True).first()
    if supplier is None:
        return
    try:
        entitlements.consume(
            supplier,
            key,
            amount=amount,
            enforce=False,
            always_track=True,
        )
    except Exception:
        logger.exception(
            "Visibility metering failed supplier=%s key=%s", supplier_id, key
        )


def meter_product_search_impression(supplier_id: int, *, request=None) -> None:
    _meter(
        supplier_id,
        entitlements.PRODUCT_IMPRESSIONS_MONTHLY,
        request=request,
    )
    from user.campaigns import bill_vitrine_search_impression

    bill_vitrine_search_impression(supplier_id, request=request)


def meter_product_page_visit(supplier_id: int, *, request=None) -> None:
    _meter(
        supplier_id,
        entitlements.PRODUCT_VISITS_MONTHLY,
        request=request,
    )
    from user.campaigns import bill_product_card_store_visit

    bill_product_card_store_visit(supplier_id, request=request)


def meter_store_page_visit(supplier_id: int, *, request=None) -> None:
    _meter(
        supplier_id,
        entitlements.STORE_VISITS_MONTHLY,
        request=request,
    )
    from user.campaigns import bill_vitrine_store_visit

    bill_vitrine_store_visit(supplier_id, request=request)


def _meter_pragmadinar_catalog_units(
    supplier,
    service_code: str,
    *,
    cache_namespace: str,
    reason_suffix: str,
) -> None:
    from django.core.cache import cache

    from user.entitlements import _period
    from user.pragmadinar import (
        UNBILLED_SERVICE_CODES,
        InsufficientPragmadinar,
        debit_service,
        get_service,
    )

    if service_code in UNBILLED_SERVICE_CODES:
        return
    service = get_service(service_code)
    unit_qty = max(int(service.unit_quantity or 1), 1)
    year, month = _period()
    cache_key = f"paknomik:{cache_namespace}:{service_code}:{supplier.pk}:{year}:{month}"
    count = int(cache.get(cache_key) or 0) + 1
    cache.set(cache_key, count, timeout=60 * 60 * 24 * 35)

    if count % unit_qty != 0:
        return

    unit_index = count // unit_qty
    try:
        debit_service(
            supplier,
            service_code,
            reason=f"{service.label} — {unit_qty} {reason_suffix}",
            idempotency_key=(
                f"{cache_namespace}:{service_code}:{supplier.pk}:{year}:{month}:u{unit_index}"
            ),
        )
    except InsufficientPragmadinar:
        logger.info(
            "Pragmadinar visibility not debited (insufficient balance) supplier=%s service=%s",
            supplier.pk,
            service_code,
        )
    except Exception:
        logger.exception(
            "Pragmadinar visibility debit failed supplier=%s service=%s",
            supplier.pk,
            service_code,
        )


def meter_pragmadinar_vitrine_visit(supplier, campaign_format: str, *, request=None) -> None:
    """Debit catalog visibility.* every N boutique visits when no campaign budget."""
    from user.campaigns import FORMAT_SERVICE

    service_code = FORMAT_SERVICE.get(campaign_format)
    if not service_code:
        return
    _meter_pragmadinar_catalog_units(
        supplier,
        service_code,
        cache_namespace="vitrine_visit",
        reason_suffix="visites boutique",
    )


def meter_pragmadinar_product_card_visit(supplier, service_code: str, *, request=None) -> None:
    """Debit visibility.card_* every N boutique / product views."""
    if not service_code:
        return
    _meter_pragmadinar_catalog_units(
        supplier,
        service_code,
        cache_namespace="card_visit",
        reason_suffix="visites (carte produit)",
    )


def meter_pragmadinar_vitrine_search_impression(
    supplier,
    service_code: str,
    *,
    request=None,
) -> None:
    """Debit visibility.*_search every N search impressions (vitrine header shown)."""
    _meter(
        supplier.pk,
        entitlements.STORE_IMPRESSIONS_MONTHLY,
        request=request,
    )
    _meter_pragmadinar_catalog_units(
        supplier,
        service_code,
        cache_namespace="vitrine_search",
        reason_suffix="vues en recherche",
    )
