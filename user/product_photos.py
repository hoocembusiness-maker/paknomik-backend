"""Product gallery pricing: 3 free photos per product, then Pragmadinar per extra slot."""

from __future__ import annotations

from rest_framework.exceptions import ValidationError

PRODUCT_PHOTOS_FREE = 3
PRODUCT_IMAGE_MAX = 15
PRODUCT_EXTRA_PHOTO_CODE = "commercial.product_extra_photo"


def paid_photo_slots(image_count: int) -> int:
    return max(0, int(image_count) - PRODUCT_PHOTOS_FREE)


def product_photo_pricing_payload():
    from user.pragmadinar import format_cost_label, get_service

    service = get_service(PRODUCT_EXTRA_PHOTO_CODE)
    return {
        "imageFreeCount": PRODUCT_PHOTOS_FREE,
        "imageMaxCount": PRODUCT_IMAGE_MAX,
        "imageExtraCostPragmadinar": str(service.cost_pragmadinar),
        "imageExtraCostLabel": format_cost_label(
            service.cost_pragmadinar,
            service.unit,
            service.unit_quantity,
            service.unit_label,
        ),
        "imageExtraServiceCode": PRODUCT_EXTRA_PHOTO_CODE,
    }


def bill_new_product_extra_photos(
    supplier,
    product,
    *,
    old_count: int,
    new_count: int,
    actor=None,
) -> None:
    delta = paid_photo_slots(new_count) - paid_photo_slots(old_count)
    if delta <= 0:
        return

    from user.pragmadinar import InsufficientPragmadinar, debit_service

    try:
        debit_service(
            supplier,
            PRODUCT_EXTRA_PHOTO_CODE,
            quantity=delta,
            ref_type="product",
            ref_id=product.pk,
            reason=(
                f"{delta} photo(s) supplémentaire(s) — "
                f"{(product.name or 'Produit')[:80]}"
            ),
            idempotency_key=(
                f"product:{product.pk}:photos:{old_count}->{new_count}"
            ),
            actor=actor,
        )
    except InsufficientPragmadinar as exc:
        raise ValidationError(
            {
                "images": (
                    f"Solde Pragmadinar insuffisant pour {delta} photo(s) "
                    f"supplémentaire(s) ({exc.required} requis, "
                    f"{exc.balance} disponible)."
                ),
                "code": "insufficient_pragmadinars",
                "required": str(exc.required),
                "balance": str(exc.balance),
            }
        ) from exc
