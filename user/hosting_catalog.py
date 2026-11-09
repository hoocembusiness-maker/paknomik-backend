"""Catalog snapshot for an empty production database.

Keeps supplier profiles and products (photos included), but never user accounts
or authentication data. Leaves out orders, messages, reviews, campaigns and
Pragmadinar history. Each imported supplier receives the welcome credit once.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from user.models import (
    Category,
    Material,
    PragmadinarLedger,
    Product,
    ProductAttributeValue,
    ProductImage,
    SearchTag,
    Service,
    Supplier,
    SupplierScore,
    SupplierSettings,
    TechnicalAttribute,
)
from user.pragmadinar import grant_supplier_welcome_pragmadinar
from user.scoring import ensure_supplier_score

CATALOG_FILENAME = "catalog.json"
MEDIA_DIRNAME = "media"

SUPPLIER_FIELDS = (
    "company_name",
    "email",
    "address",
    "wilaya",
    "subscription_type",
    "subscription_start_date",
    "subscription_end_date",
    "paknomik_plan",
    "type",
    "website",
    "instagram_url",
    "facebook_url",
    "linkedin_url",
    "whatsapp_number",
    "presentation",
    "presentation_html",
    "brand_color",
    "brand_button_color",
    "brand_neutral_color",
    "is_active",
    "is_featured",
    "is_ghost",
    "created_at",
)

PRODUCT_FIELDS = (
    "name",
    "name_ar",
    "description",
    "description_html",
    "price",
    "price_max",
    "min_order_quantity",
    "lead_time_days",
    "listing_type",
    "is_active",
    "is_featured",
    "custom_keywords",
    "target_markets",
    "created_at",
)

SCORE_FIELDS = (
    "score_total",
    "score_presence",
    "score_responsivity",
    "score_catalog",
    "score_profile",
    "score_commercial",
    "profile_bonuses_awarded",
)


def catalog_path(root: Path) -> Path:
    return root / CATALOG_FILENAME


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _row(instance, fields):
    data = {}
    for name in fields:
        data[name] = _json_value(getattr(instance, name))
    return data


def _media_root() -> Path:
    return Path(settings.MEDIA_ROOT)


def _safe_media_file(relative_name: str) -> Path | None:
    if not relative_name or relative_name.startswith(("/", "\\")) or ".." in Path(relative_name).parts:
        return None
    root = _media_root().resolve()
    candidate = (root / relative_name).resolve()
    if root not in candidate.parents and candidate != root:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _queue_media(bucket: set[str], relative_name: str | None):
    if relative_name and _safe_media_file(relative_name):
        bucket.add(relative_name.replace("\\", "/"))


def export_hosting_catalog(dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    media_dest = dest / MEDIA_DIRNAME
    if media_dest.exists():
        shutil.rmtree(media_dest)
    media_dest.mkdir()

    media_names: set[str] = set()
    suppliers_payload = []
    skipped_products = []

    suppliers = (
        Supplier.objects.order_by("id")
        .prefetch_related(
            "services",
            "interested_categories",
            "interested_subcategories",
            "products__images",
            "products__attribute_values__attribute",
            "products__search_tags",
            "products__category",
            "products__subcategory",
            "products__material",
        )
        .select_related("settings", "score_record")
    )

    for supplier in suppliers:
        _queue_media(media_names, supplier.logo.name if supplier.logo else None)
        products_payload = []
        for product in supplier.products.all().order_by("id"):
            category_slug = product.category.slug if product.category_id else None
            if not category_slug:
                skipped_products.append(product.pk)
                continue
            images = []
            for image in product.images.all().order_by("id"):
                relative = (image.image.name or "").replace("\\", "/")
                _queue_media(media_names, relative)
                images.append({
                    "file": relative,
                    "is_primary": image.is_primary,
                    "created_at": _json_value(image.created_at),
                })
            attributes = []
            for value in product.attribute_values.all().order_by("id"):
                attributes.append({
                    "external_id": value.attribute.external_id,
                    "name_en": value.attribute.name_en,
                    "num_value": value.num_value,
                    "txt_value": value.txt_value,
                    "bool_value": value.bool_value,
                })
            products_payload.append({
                "source_id": product.pk,
                "category_slug": category_slug,
                "subcategory_slug": product.subcategory.slug if product.subcategory_id else None,
                "material_external_id": product.material.external_id if product.material_id else None,
                "material_name_en": product.material.name_en if product.material_id else None,
                "search_tags": list(product.search_tags.order_by("tag").values_list("tag", flat=True)),
                "fields": _row(product, PRODUCT_FIELDS),
                "images": images,
                "attributes": attributes,
            })

        settings_row = None
        try:
            settings_row = {
                "product_card_style": supplier.settings.product_card_style,
                "faq_items": supplier.settings.faq_items,
                "listing_form_fields": supplier.settings.listing_form_fields,
                "listing_card_fields": supplier.settings.listing_card_fields,
                "listing_rules": supplier.settings.listing_rules,
                "proposal_template": supplier.settings.proposal_template,
                "proposal_blocks": supplier.settings.proposal_blocks,
                "proposal_templates": supplier.settings.proposal_templates,
                "page_design": supplier.settings.page_design,
            }
        except SupplierSettings.DoesNotExist:
            settings_row = None

        score_row = None
        try:
            score_row = _row(supplier.score_record, SCORE_FIELDS)
        except SupplierScore.DoesNotExist:
            score_row = None

        suppliers_payload.append({
            "fields": _row(supplier, SUPPLIER_FIELDS),
            "logo": (supplier.logo.name or "").replace("\\", "/") if supplier.logo else "",
            "interested_category_slugs": list(
                supplier.interested_categories.order_by("slug").values_list("slug", flat=True)
            ),
            "interested_subcategory_slugs": list(
                supplier.interested_subcategories.order_by("slug").values_list("slug", flat=True)
            ),
            "services": [
                {"name": service.name, "description": service.description}
                for service in supplier.services.all().order_by("id")
            ],
            "settings": settings_row,
            "score": score_row,
            "products": products_payload,
        })

    copied = 0
    for relative in sorted(media_names):
        source = _safe_media_file(relative)
        if source is None:
            continue
        target = media_dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1

    payload = {
        "version": 1,
        "includes": ["suppliers", "supplier_accounts", "products", "product_photos", "logos"],
        "excludes": [
            "orders",
            "messages",
            "reviews",
            "campaigns",
            "pragmadinar_ledger",
            "buyer_accounts",
        ],
        "suppliers": suppliers_payload,
        "skipped_product_ids": skipped_products,
    }
    catalog_path(dest).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "suppliers": len(suppliers_payload),
        "products": sum(len(row["products"]) for row in suppliers_payload),
        "media_files": copied,
        "skipped_products": len(skipped_products),
    }


def _assign(instance, fields, payload):
    for name in fields:
        if name not in payload:
            continue
        value = payload[name]
        field = instance._meta.get_field(name)
        if value in (None, "") and getattr(field, "null", False) and name.endswith("_at"):
            setattr(instance, name, None)
            continue
        if isinstance(value, str) and field.get_internal_type() in {"DateTimeField"}:
            setattr(instance, name, parse_datetime(value))
            continue
        if isinstance(value, str) and field.get_internal_type() == "DecimalField":
            setattr(instance, name, Decimal(value) if value else None)
            continue
        setattr(instance, name, value)


def _copy_bundle_media(src: Path) -> int:
    bundle_media = src / MEDIA_DIRNAME
    if not bundle_media.is_dir():
        return 0
    root = _media_root()
    copied = 0
    for source in bundle_media.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(bundle_media)
        if ".." in relative.parts:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def _category_by_slug(slug: str | None) -> Category | None:
    if not slug:
        return None
    return Category.objects.filter(slug=slug).first()


def _material(external_id: str | None, name_en: str | None) -> Material | None:
    if external_id:
        found = Material.objects.filter(external_id=external_id).first()
        if found:
            return found
    if name_en:
        return Material.objects.filter(name_en=name_en).first()
    return None


def _attribute(external_id: str | None, name_en: str | None) -> TechnicalAttribute | None:
    if external_id:
        found = TechnicalAttribute.objects.filter(external_id=external_id).first()
        if found:
            return found
    if name_en:
        return TechnicalAttribute.objects.filter(name_en=name_en).first()
    return None


@transaction.atomic
def _import_supplier(row: dict) -> tuple[int, int]:
    fields = dict(row["fields"])
    created_at = parse_datetime(fields["created_at"]) if fields.get("created_at") else None
    email = fields["email"]
    supplier = Supplier(email=email, company_name=fields.get("company_name") or email)
    _assign(supplier, SUPPLIER_FIELDS, fields)
    logo = (row.get("logo") or "").replace("\\", "/")
    if logo and (_media_root() / logo).is_file():
        supplier.logo.name = logo
    supplier.save()
    if created_at:
        Supplier.objects.filter(pk=supplier.pk).update(created_at=created_at, updated_at=created_at)

    category_slugs = [slug for slug in row.get("interested_category_slugs") or [] if slug]
    subcategory_slugs = [slug for slug in row.get("interested_subcategory_slugs") or [] if slug]
    if category_slugs:
        supplier.interested_categories.set(Category.objects.filter(slug__in=category_slugs))
    if subcategory_slugs:
        supplier.interested_subcategories.set(Category.objects.filter(slug__in=subcategory_slugs))

    for service_row in row.get("services") or []:
        Service.objects.create(
            supplier=supplier,
            name=service_row.get("name") or "",
            description=service_row.get("description") or "",
        )

    settings_row = row.get("settings")
    if settings_row:
        SupplierSettings.objects.update_or_create(
            supplier=supplier,
            defaults=settings_row,
        )

    score = ensure_supplier_score(supplier.pk)
    score_row = row.get("score") or {}
    for name in SCORE_FIELDS:
        if name in score_row and score_row[name] is not None:
            value = score_row[name]
            if name.startswith("score_") and isinstance(value, str):
                value = int(value)
            setattr(score, name, value)
    score.last_login_date = None
    score.presence_streak = 0
    score.save()

    product_count = 0
    for product_row in row.get("products") or []:
        category = _category_by_slug(product_row.get("category_slug"))
        if category is None:
            continue
        product = Product(
            supplier=supplier,
            category=category,
            subcategory=_category_by_slug(product_row.get("subcategory_slug")),
            material=_material(
                product_row.get("material_external_id"),
                product_row.get("material_name_en"),
            ),
        )
        product_fields = product_row.get("fields") or {}
        product_created = parse_datetime(product_fields["created_at"]) if product_fields.get("created_at") else None
        _assign(product, PRODUCT_FIELDS, product_fields)
        product.save()
        if product_created:
            Product.objects.filter(pk=product.pk).update(
                created_at=product_created,
                updated_at=product_created,
            )
        tags = list(
            SearchTag.objects.filter(tag__in=product_row.get("search_tags") or [])
        )
        if tags:
            product.search_tags.set(tags)
        for image_row in product_row.get("images") or []:
            relative = (image_row.get("file") or "").replace("\\", "/")
            if not relative or not (_media_root() / relative).is_file():
                continue
            image = ProductImage(product=product, is_primary=bool(image_row.get("is_primary")))
            image.image.name = relative
            image.save()
        for attribute_row in product_row.get("attributes") or []:
            attribute = _attribute(attribute_row.get("external_id"), attribute_row.get("name_en"))
            if attribute is None:
                continue
            ProductAttributeValue.objects.create(
                product=product,
                attribute=attribute,
                num_value=attribute_row.get("num_value"),
                txt_value=attribute_row.get("txt_value") or "",
                bool_value=bool(attribute_row.get("bool_value")),
            )
        product_count += 1

    grant_supplier_welcome_pragmadinar(supplier)
    return 1, product_count


def import_hosting_catalog(src: Path) -> dict:
    """Copy catalog photos, then load suppliers and products when the database has none."""
    path = catalog_path(src)
    if not path.is_file():
        return {"loaded": False, "reason": "missing"}

    media_files = _copy_bundle_media(src)
    if Supplier.objects.exists():
        return {
            "loaded": False,
            "reason": "suppliers_already_present",
            "media_files": media_files,
        }

    payload = json.loads(path.read_text(encoding="utf-8"))
    supplier_count = 0
    product_count = 0
    for row in payload.get("suppliers") or []:
        added_suppliers, added_products = _import_supplier(row)
        supplier_count += added_suppliers
        product_count += added_products

    return {
        "loaded": True,
        "reason": "imported",
        "suppliers": supplier_count,
        "products": product_count,
        "media_files": media_files,
        "ledger_rows": PragmadinarLedger.objects.count(),
    }
