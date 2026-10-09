"""Sample requests: supplier settings stored in ``SupplierSettings.page_design``."""

from __future__ import annotations

DEFAULT_SAMPLE_QUANTITY = 5
MIN_SAMPLE_QUANTITY = 1
MAX_SAMPLE_QUANTITY = 50


def normalize_sample_quantity(value) -> int:
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_QUANTITY
    return max(MIN_SAMPLE_QUANTITY, min(MAX_SAMPLE_QUANTITY, quantity))


def normalize_samples_enabled(value) -> bool:
    """Enabled unless explicitly turned off."""
    if value is None or value == "":
        return True
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def sample_settings(supplier) -> dict:
    settings_obj = getattr(supplier, "settings", None)
    page = (getattr(settings_obj, "page_design", None) or {}) if settings_obj else {}
    return {
        "enabled": normalize_samples_enabled(page.get("samplesEnabled")),
        "quantity": normalize_sample_quantity(
            page.get("sampleQuantity", DEFAULT_SAMPLE_QUANTITY)
        ),
    }
