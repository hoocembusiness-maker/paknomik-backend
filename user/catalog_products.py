"""Create and update a gamme without going through the historical product form."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework.exceptions import ValidationError

from user.catalog_taxonomy import (
    OFFER_STOCK,
    OFFER_SUR_MESURE,
    SchemaError,
    default_offer_mode,
    required_covered,
    stock_publish_error,
    validate_attributes,
    validate_axes,
)
from user.models import Product, ProductImage, ProductType, ProductVariant
from user.product_photos import PRODUCT_IMAGE_MAX, bill_new_product_extra_photos


def _legacy_categories(product_type: ProductType):
    legacy = product_type.legacy_category
    if legacy is None:
        return None, None
    if legacy.parent_id:
        return legacy.parent, legacy
    return legacy, None


def _variants_from_payload(raw) -> list[dict]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValidationError({"variants": "Les variantes doivent être une liste."})
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValidationError({"variants": "Variante illisible."})
        price = item.get("priceFrom", item.get("price_from"))
        moq = item.get("moq")
        if price in ("", None):
            price_value = None
        else:
            try:
                price_value = Decimal(str(price))
            except (InvalidOperation, ValueError) as exc:
                raise ValidationError({"variants": "Prix de départ invalide."}) from exc
        if moq in ("", None):
            moq_value = None
        else:
            try:
                moq_value = int(moq)
            except (TypeError, ValueError) as exc:
                raise ValidationError({"variants": "Quantité minimum invalide."}) from exc
            if moq_value < 1:
                raise ValidationError({"variants": "Quantité minimum invalide."})
        cleaned.append(
            {
                "id": item.get("id"),
                "sku": str(item.get("sku") or "")[:64],
                "axes": item.get("axes") or {},
                "price_from": price_value,
                "moq": moq_value,
                "is_available": bool(item.get("available", item.get("is_available", True))),
            }
        )
    return cleaned


def _sync_listing(product: Product) -> None:
    if product.offer_mode == OFFER_SUR_MESURE and not product.is_mixed:
        product.listing_type = Product.ListingType.MAKE
    elif product.offer_mode in {OFFER_STOCK, OFFER_SUR_MESURE}:
        product.listing_type = Product.ListingType.SELL


def _sync_price(product: Product, variants: list[dict]) -> None:
    priced = [
        item
        for item in variants
        if item.get("is_available", True)
        and item.get("price_from") is not None
        and item.get("moq")
    ]
    if not priced:
        return
    floor = min(priced, key=lambda item: item["price_from"])
    product.price = floor["price_from"]
    product.min_order_quantity = floor["moq"]


def _flagship(product: Product) -> None:
    if not product.is_flagship or not product.product_type_id:
        return
    (
        Product.objects.filter(
            supplier_id=product.supplier_id,
            product_type_id=product.product_type_id,
            is_flagship=True,
        )
        .exclude(pk=product.pk)
        .update(is_flagship=False)
    )


@transaction.atomic
def save_gamme(
    *,
    supplier,
    payload: dict,
    files,
    actor,
    instance: Product | None = None,
) -> Product:
    type_id = payload.get("productTypeId") or payload.get("product_type_id")
    if instance is not None and not type_id:
        product_type = instance.product_type
    else:
        product_type = ProductType.objects.filter(pk=type_id).first()
    if product_type is None:
        raise ValidationError({"productTypeId": "Choisissez un type de produit."})

    offer_mode = payload.get("offerMode") or payload.get("offer_mode") or ""
    if not offer_mode:
        offer_mode = (
            instance.offer_mode
            if instance is not None and instance.offer_mode
            else default_offer_mode(supplier.kind)
        )
    if offer_mode not in {OFFER_STOCK, OFFER_SUR_MESURE}:
        raise ValidationError({"offerMode": "Mode inconnu."})

    name = (payload.get("name") or (instance.name if instance else "")).strip()
    if not name:
        raise ValidationError({"name": "Le nom de la gamme est obligatoire."})
    description = payload.get("description")
    if description is None:
        description = instance.description if instance else ""

    publishing = _as_bool(payload.get("publish", True if instance is None else instance.is_active))
    schema = list(product_type.attribute_schema or [])
    try:
        attributes = validate_attributes(
            schema,
            payload.get("attributes", instance.attributes if instance else {}),
            offer_mode=offer_mode,
            publishing=False,
        )
        variants = _variants_from_payload(payload.get("variants"))
        for item in variants:
            item["axes"] = validate_axes(schema, item["axes"], offer_mode=offer_mode)
    except SchemaError as exc:
        raise ValidationError({"attributes": exc.messages}) from exc

    if "variants" not in payload and instance is not None:
        variants = [
            {
                "id": row.pk,
                "sku": row.sku,
                "axes": row.axes or {},
                "price_from": row.price_from,
                "moq": row.moq,
                "is_available": row.is_available,
            }
            for row in instance.variants.all()
        ]

    if publishing:
        missing = required_covered(schema, attributes, variants)
        if missing:
            raise ValidationError(
                {"attributes": [f"{label} est obligatoire." for label in missing]}
            )
        if offer_mode == OFFER_STOCK:
            problem = stock_publish_error(variants)
            if problem:
                raise ValidationError({"offerMode": problem, "suggestOfferMode": OFFER_SUR_MESURE})

    category, subcategory = _legacy_categories(product_type)
    if instance is None:
        if category is None:
            raise ValidationError(
                {
                    "productTypeId": (
                        "Ce type n'est pas encore relié à une catégorie du catalogue actuel."
                    )
                }
            )
        product = Product(
            supplier=supplier,
            category=category,
            subcategory=subcategory,
            name=name,
            description=description or name,
        )
    else:
        product = instance
        product.name = name
        if description is not None:
            product.description = description or name
        if category is not None:
            product.category = category
            product.subcategory = subcategory

    product.product_type = product_type
    product.offer_mode = offer_mode
    product.attributes = attributes
    product.is_mixed = _as_bool(payload.get("mixed", product.is_mixed if instance else False))
    product.is_flagship = _as_bool(
        payload.get("flagship", product.is_flagship if instance else False)
    )
    product.is_active = publishing
    sheet = files.get("technicalSheet") or files.get("technical_sheet")
    if sheet is not None:
        if not str(getattr(sheet, "name", "")).lower().endswith(".pdf"):
            raise ValidationError({"technicalSheet": "La fiche technique doit être un PDF."})
        product.technical_sheet = sheet
    _sync_listing(product)
    _sync_price(product, variants)
    product.save()
    _flagship(product)

    if "variants" in payload:
        _replace_variants(product, variants)

    _store_images(product, supplier, files, actor)
    return product


def _replace_variants(product: Product, variants: list[dict]) -> None:
    kept = []
    for item in variants:
        variant_id = item.get("id")
        row = None
        if variant_id:
            row = product.variants.filter(pk=variant_id).first()
        if row is None:
            row = ProductVariant(product=product)
        row.sku = item["sku"]
        row.axes = item["axes"]
        row.price_from = item["price_from"]
        row.moq = item["moq"]
        row.is_available = item["is_available"]
        row.save()
        kept.append(row.pk)
    product.variants.exclude(pk__in=kept).delete()


def _store_images(product, supplier, files, actor) -> None:
    uploads = list(files.getlist("images")) if hasattr(files, "getlist") else []
    single = files.get("image") if hasattr(files, "get") else None
    if single is not None and single not in uploads:
        uploads.insert(0, single)
    if not uploads:
        return
    old_count = product.images.count()
    if old_count + len(uploads) > PRODUCT_IMAGE_MAX:
        raise ValidationError({"images": f"Maximum {PRODUCT_IMAGE_MAX} photo(s) par produit."})
    for upload in uploads:
        ProductImage.objects.create(
            product=product,
            image=upload,
            is_primary=old_count == 0 and upload is uploads[0],
        )
    bill_new_product_extra_photos(
        supplier,
        product,
        old_count=old_count,
        new_count=old_count + len(uploads),
        actor=actor,
    )


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}
