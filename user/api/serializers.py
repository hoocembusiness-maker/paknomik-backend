"""
Converts Django models to JSON (and back) for the API.
Controls which fields the frontend sees and validates input.
"""

from rest_framework import serializers
import json
import re
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from user.category_tree import subcategory_belongs_to_category
from user.order_reviews import RATEABLE_STATUSES, delivery_review_stats
from user.order_workflow import (
    BUYER_CANCELLABLE,
    ORDER_STATUS_LABELS,
    SUPPLIER_ARCHIVABLE,
    SUPPLIER_REOPEN_TRANSITIONS,
    serialize_status_history,
    supplier_allowed_transitions,
)
from user.product_completeness import calculate_product_completeness
from user.product_markets import normalize_target_markets, target_market_labels
from user.product_similarity import find_similar_products
from user.response_time import format_response_time, supplier_response_minutes
from user.rich_text import RICH_HTML_MAX_LENGTH
from user.search_tag_utils import (
    apply_product_search_tags,
    normalize_supplier_keyword,
    promote_product_custom_keywords,
    resolve_search_tag_ids,
)
from user.scoring import ensure_supplier_score, get_supplier_rank
from user.models import (
    AttributeOption,
    BuyerFavoriteSupplier,
    BuyerSearchHistory,
    Category,
    CategoryAttribute,
    Event,
    Product,
    ProductAttributeValue,
    ProductVariant,
    ProductImage,
    RFQRequest,
    QuoteResponse,
    Order,
    OrderResponse,
    OrderSupplierReview,
    Message,
    SampleRequest,
    SearchTag,
    SupportMessage,
    Supplier,
    SupplierSettings,
    TechnicalAttribute,
)
from user.samples import (
    DEFAULT_SAMPLE_QUANTITY,
    normalize_sample_quantity,
    normalize_samples_enabled,
    sample_settings,
)

# Limits per subscription tier (enforced when subscription_type exists on Supplier).
SEARCH_TAG_LIMITS = {
    "classic": 64,
    "extra": 64,
    "gold": 64,
}

PRODUCT_IMAGE_LIMITS = {
    "classic": 1,
    "extra": 4,
    "gold": 10,
}

# Legacy tier map kept for reference; enforced max comes from product_photos.PRODUCT_IMAGE_MAX.

CUSTOM_KEYWORD_MAX_LENGTH = 80


def _normalize_custom_keywords(values):
    """Clean supplier free-text keywords (dedupe, trim, max length)."""
    if not values:
        return []
    seen = set()
    normalized = []
    for raw in values:
        word = normalize_supplier_keyword(raw)
        if not word or len(word) > CUSTOM_KEYWORD_MAX_LENGTH:
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(word)
    return normalized


def _validate_product_keyword_totals(resolved_tag_ids, limit):
    total = len(resolved_tag_ids or [])
    if total > limit:
        raise serializers.ValidationError(
            {
                "searchTagIds": (
                    f"Maximum {limit} mots-clés au total "
                    f"(catalogue + personnalisés)."
                )
            }
        )


def get_supplier_subscription_tier(supplier):
    raw = (
        getattr(supplier, "subscription_type", None)
        or getattr(supplier, "subscription_tier", None)
        or "Classic"
    )
    tier = str(raw).strip().lower()
    if tier not in SEARCH_TAG_LIMITS:
        return "classic"
    return tier


def get_supplier_search_tag_limit(supplier):
    tier = get_supplier_subscription_tier(supplier)
    return SEARCH_TAG_LIMITS.get(tier, SEARCH_TAG_LIMITS["classic"])


def get_supplier_product_image_limit(supplier):
    from user.product_photos import PRODUCT_IMAGE_MAX

    del supplier
    return PRODUCT_IMAGE_MAX


def ensure_supplier_settings(supplier):
    settings_obj, _ = SupplierSettings.objects.get_or_create(supplier=supplier)
    return settings_obj


def _normalize_proposal_templates(raw_templates, legacy_blocks=None):
    """Normalize stored proposal templates; migrate legacy single blocks list if needed."""
    templates = []
    if isinstance(raw_templates, list):
        for item in raw_templates:
            if not isinstance(item, dict):
                continue
            blocks = item.get("blocks")
            if not isinstance(blocks, list) or not blocks:
                continue
            name = (item.get("name") or "").strip() or "Modèle sans nom"
            templates.append(
                {
                    "id": str(item.get("id") or f"tpl_{len(templates)}"),
                    "name": name,
                    "isDefault": bool(item.get("isDefault")),
                    "status": item.get("status") or "active",
                    "presetId": item.get("presetId"),
                    "theme": item.get("theme"),
                    "updatedAt": item.get("updatedAt"),
                    "paid": bool(item.get("paid")),
                    "blocks": blocks,
                }
            )
    if not templates and isinstance(legacy_blocks, list) and legacy_blocks:
        templates = [
            {
                "id": "default",
                "name": "Modèle par défaut",
                "isDefault": True,
                "blocks": legacy_blocks,
            }
        ]
    if templates and not any(t.get("isDefault") for t in templates):
        templates[0]["isDefault"] = True
    return templates


def _default_proposal_template(templates):
    if not templates:
        return None
    for tpl in templates:
        if tpl.get("isDefault"):
            return tpl
    return templates[0]


INCLUDED_PROPOSAL_TEMPLATES = 1


def _merge_proposal_template_paid_flags(old_templates, new_templates):
    """Never trust the client for `paid` — only preserve flags already stored server-side."""
    old_paid = {str(t.get("id")): bool(t.get("paid")) for t in old_templates}
    merged = []
    for item in new_templates:
        tpl_id = str(item.get("id"))
        merged.append({**item, "paid": old_paid.get(tpl_id, False)})
    return merged


def _enforce_proposal_template_billing(old_templates, new_templates):
    """One template included; each extra slot must be bought via /templates/extra/."""
    merged = _merge_proposal_template_paid_flags(old_templates, new_templates)
    paid_slots = sum(1 for t in merged if t.get("paid"))
    max_allowed = INCLUDED_PROPOSAL_TEMPLATES + paid_slots
    if len(merged) > max_allowed:
        raise serializers.ValidationError(
            {
                "proposalTemplates": (
                    "Chaque modèle supplémentaire coûte 5 Pragmadinar. "
                    "Confirmez le débit avant d'ajouter un nouveau modèle."
                ),
                "code": "extra_template_payment_required",
            }
        )
    return merged


def _faq_items_from_proposal_blocks(blocks):
    faq_items = []
    if not isinstance(blocks, list):
        return faq_items
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "faq":
            continue
        for item in block.get("faqItems") or []:
            if not isinstance(item, dict):
                continue
            question = (item.get("question") or "").strip()
            answer = (item.get("answer") or "").strip()
            if question and answer:
                faq_items.append({"question": question, "answer": answer})
    return faq_items


def render_proposal_blocks_to_text(blocks):
    """Render enabled proposal blocks to plain text for email / stored message."""
    if not isinstance(blocks, list):
        return ""
    parts = []
    for block in blocks:
        if not isinstance(block, dict) or not block.get("enabled", True):
            continue
        title = (block.get("title") or "").strip()
        btype = block.get("type")
        if title:
            parts.append(title.upper())
        if btype == "header":
            content = (block.get("content") or "").strip()
            if content:
                parts.append(content)
        elif btype == "product":
            for item in block.get("items") or []:
                if not isinstance(item, dict):
                    continue
                label = (item.get("label") or "").strip()
                value = (item.get("value") or "").strip()
                if label or value:
                    parts.append(f"- {label}: {value}" if label else f"- {value}")
        elif btype == "process":
            for i, step in enumerate(block.get("steps") or [], start=1):
                step_text = (step or "").strip()
                if step_text:
                    parts.append(f"{i}. {step_text}")
        elif btype == "faq":
            for item in block.get("faqItems") or []:
                if not isinstance(item, dict):
                    continue
                question = (item.get("question") or "").strip()
                answer = (item.get("answer") or "").strip()
                if question and answer:
                    parts.append(f"Q: {question}\nR: {answer}")
        elif btype == "pricing":
            content = (block.get("content") or "").strip()
            if content:
                parts.append(content)
        elif btype == "terms":
            content = (block.get("content") or "").strip()
            if content:
                parts.append(content)
        parts.append("")
    return "\n".join(parts).strip()


def _brand_neutral_text_colors(supplier) -> dict[str, str]:
    neutral = (getattr(supplier, "brand_neutral_color", None) or "#ffffff").strip().lower()
    if neutral in ("#000000", "#000", "black", "noir"):
        return {
            "titleColor": "#000000",
            "specLabelColor": "rgba(0, 0, 0, 0.85)",
        }
    return {
        "titleColor": "#ffffff",
        "specLabelColor": "rgba(255, 255, 255, 0.85)",
    }


def resolved_product_card_layout(supplier) -> str:
    """Layout stored on product_card_style and/or page_design (boutique settings)."""
    settings_obj = getattr(supplier, "settings", None)
    if settings_obj is None:
        try:
            settings_obj = supplier.settings
        except SupplierSettings.DoesNotExist:
            return "vertical"
    style = dict(settings_obj.product_card_style or {})
    page = dict(getattr(settings_obj, "page_design", None) or {})
    raw = str(
        style.get("layout") or page.get("productCardLayout") or "vertical"
    ).lower()
    if raw in {"square", "passport", "carre", "carré"}:
        return "square"
    if raw in {"horizontal", "central"}:
        return "horizontal"
    if raw in {"vertical", "square", "horizontal"}:
        return raw
    return "vertical"


def get_supplier_product_card_style(supplier):
    settings_obj = getattr(supplier, "settings", None)
    if settings_obj is None:
        try:
            settings_obj = supplier.settings
        except SupplierSettings.DoesNotExist:
            return None
    style = dict(settings_obj.product_card_style or {})
    page = dict(getattr(settings_obj, "page_design", None) or {})
    if not style and not page.get("productCardLayout"):
        return None
    style["layout"] = resolved_product_card_layout(supplier)
    if not str(style.get("useBrandOnCard") or "").strip():
        style["useBrandOnCard"] = page.get("useBrandOnCard", "false")
    style["brandColor"] = (
        page.get("coverColor") or getattr(supplier, "brand_color", None) or "#6c3b9e"
    ).strip()
    style["brandButtonColor"] = (
        getattr(supplier, "brand_button_color", None) or ""
    ).strip()
    style["brandNeutralColor"] = (
        getattr(supplier, "brand_neutral_color", None) or "#ffffff"
    ).strip()
    style.update(_brand_neutral_text_colors(supplier))
    return style


def _supplier_search_boutique_design(supplier):
    """Extra / Central / Mega header in search when opted in (not Standard)."""
    settings_obj = getattr(supplier, "settings", None)
    if settings_obj is None:
        try:
            settings_obj = supplier.settings
        except SupplierSettings.DoesNotExist:
            return None
    raw = settings_obj.page_design or {}
    enabled = str(raw.get("useStoreHeaderInSearch", "")).lower() in (
        "true",
        "1",
        "yes",
    )
    if not enabled:
        return None
    tier = str(raw.get("storeHeaderTier") or "standard").lower()
    if tier not in {"extra", "central", "mega"}:
        return None
    cover = (raw.get("coverColor") or supplier.brand_color or "").strip()
    show_stats = str(raw.get("showStats", "true")).lower() not in (
        "false",
        "0",
        "no",
    )
    show_rating = str(raw.get("showRating", "true")).lower() not in (
        "false",
        "0",
        "no",
    )
    return {
        "tier": tier,
        "coverColor": cover,
        "brandNeutralColor": (supplier.brand_neutral_color or "#ffffff").strip(),
        "showStats": show_stats,
        "showRating": show_rating,
        "logoZoom": raw.get("logoZoom") or "1",
        "logoOffsetX": raw.get("logoOffsetX") or "0",
        "logoOffsetY": raw.get("logoOffsetY") or "0",
    }


def _flag_enabled(value) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes")


def get_supplier_product_card_style_for_search(supplier):
    """Return the saved card design in search only when the supplier opted in."""
    style = get_supplier_product_card_style(supplier)
    if not style:
        return None
    settings_obj = getattr(supplier, "settings", None)
    page = dict(getattr(settings_obj, "page_design", None) or {}) if settings_obj else {}
    layout = str(style.get("layout") or "vertical").lower()
    if layout == "vertical":
        return None
    if not (
        _flag_enabled(style.get("useInSearch"))
        or _flag_enabled(page.get("useProductCardInSearch"))
    ):
        return None
    return style


def _format_price_amount(price):
    if price == int(price):
        return f"{int(price):,}".replace(",", " ")
    return f"{price:,.2f}".replace(",", " ").replace(".", ",")


def _product_price_badge(product):
    if product.price is None:
        return "Négociable"
    if product.has_price_range:
        return (
            f"{_format_price_amount(product.price)} – "
            f"{_format_price_amount(product.price_max)} DA / unité"
        )
    return f"{_format_price_amount(product.price)} DA / unité"


def _product_moq_label(product):
    if product.min_order_quantity <= 1:
        return "Variable"
    qty = f"{product.min_order_quantity:,}".replace(",", " ")
    return f"{qty} unités"


def _product_delivery_label(product):
    if product.lead_time_days:
        return f"{product.lead_time_days} jours"
    return "Selon la quantité"


def _plain_number(value) -> str:
    """Number without scientific notation (1500000, 12.5, 0.00001)."""
    text = format(Decimal(str(value)).normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _attribute_display_value(av):
    if av.num_value is not None:
        unit = ""
        if av.attribute.unit_id and av.attribute.unit:
            unit = av.attribute.unit.symbol or av.attribute.unit.name or ""
        formatted = _plain_number(av.num_value)
        if unit and unit.lower() not in formatted.lower():
            return f"{formatted}{unit}"
        return formatted
    if av.txt_value:
        return av.txt_value.strip()
    return None


_DIMS_IN_NAME_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[×xX]\s*(\d+(?:[.,]\d+)?)\s*[×xX]\s*(\d+(?:[.,]\d+)?)\s*cm",
    re.IGNORECASE,
)
_MISSING_DIMENSIONS = frozenset({"a definir", "à définir", "a définir", "—"})


def _dimensions_from_product_name(name):
    match = _DIMS_IN_NAME_RE.search(name or "")
    if not match:
        return None
    return f"{match.group(1)} × {match.group(2)} × {match.group(3)} cm"


def _dimensions_from_stored_attributes(product):
    attrs = getattr(product, "attributes", None) or {}
    if not isinstance(attrs, dict):
        return None
    stored = str(attrs.get("dimensions") or "").strip()
    if stored and stored.casefold() not in _MISSING_DIMENSIONS:
        return stored
    return None


def _dimensions_from_probox_spec(name):
    from user.boite_compartiment import _cm_label, _spec_for

    spec = _spec_for(name or "")
    if spec is None:
        return None
    return _cm_label(spec)


def _normalize_dim_key(attr):
    names = " ".join(
        filter(
            None,
            [
                (attr.name_en or "").lower(),
                (attr.name_fr or "").lower(),
            ],
        )
    )
    if any(k in names for k in ("longueur", "length")) and "tube" not in names:
        return "length"
    if any(k in names for k in ("largeur", "width")) and "soufflet" not in names:
        if "gusset" not in names:
            return "width"
    if any(k in names for k in ("hauteur", "height")):
        if "can height" not in names:
            return "height"
    if any(k in names for k in ("soufflet", "gusset", "profondeur", "depth")):
        return "depth"
    if "dimension" in names and "plat" not in names:
        return "combined"
    return None


def _product_dimensions(product):
    values = getattr(product, "_prefetched_objects_cache", {}).get("attribute_values")
    if values is None:
        values = product.attribute_values.select_related("attribute", "attribute__unit").all()

    parts = {}
    combined = None
    for group in _group_attribute_values(values):
        key = _normalize_dim_key(group[0].attribute)
        if not key:
            continue
        display = _join_attribute_values(group)
        if not display:
            continue
        if key == "combined":
            combined = display
        else:
            parts[key] = display

    if combined:
        return combined

    length = parts.get("length")
    width = parts.get("width")
    height = parts.get("height")
    depth = parts.get("depth")

    if length and width and height:
        return f"{length} × {width} × {height}"
    if width and height and depth:
        return f"{width} × {height} × {depth}"
    if width and height:
        return f"{width} × {height}"

    for av in values:
        attr_name = (av.attribute.name_en or av.attribute.name_fr or "").lower()
        if "dimension" in attr_name:
            if av.txt_value:
                return av.txt_value
            if av.num_value is not None:
                unit = av.attribute.unit.symbol if av.attribute.unit_id else "mm"
                return f"{_plain_number(av.num_value)}{unit}"

    stored = _dimensions_from_stored_attributes(product)
    if stored:
        return stored
    from_name = _dimensions_from_product_name(getattr(product, "name", "") or "")
    if from_name:
        return from_name
    from_spec = _dimensions_from_probox_spec(getattr(product, "name", "") or "")
    if from_spec:
        return from_spec
    return "A définir"


def _product_image_url(product, request):
    image = product.get_display_image()
    if not image:
        return None
    url = image.url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def _product_secondary_image_url(product, request):
    """Second uploaded photo (after the primary one), used by the 2-photo horizontal card."""
    images = list(product.images.all())
    if len(images) < 2:
        return None
    url = images[1].image.url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def _attribute_category_for_validation(category, subcategory):
    if subcategory and CategoryAttribute.objects.filter(category=subcategory).exists():
        return subcategory
    if CategoryAttribute.objects.filter(category=category).exists():
        return category
    if category.parent_id:
        return category.parent
    return category


def _attribute_allowed_on_product(attribute, category, subcategory) -> bool:
    """Accept attrs linked on the subcategory or its parent family."""
    categories = []
    if subcategory is not None:
        categories.append(subcategory)
        if getattr(subcategory, "parent_id", None):
            categories.append(subcategory.parent)
    if category is not None and category not in categories:
        categories.append(category)
        if getattr(category, "parent_id", None) and category.parent not in categories:
            categories.append(category.parent)
    if not categories:
        return False
    return CategoryAttribute.objects.filter(
        category__in=categories, attribute=attribute
    ).exists()


def _parse_json_form_list(value):
    """Parse JSON list fields sent as strings in multipart form data."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], str):
            value = value[0]
        else:
            return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _drop_stale_rich_html(instance, validated_data, html_attr: str, text_attr: str) -> None:
    """A client that edits only the plain text (admin, legacy forms) replaces the
    formatted version; sending the unchanged text keeps it."""
    if html_attr in validated_data or text_attr not in validated_data:
        return
    current = (getattr(instance, text_attr, "") or "").strip()
    if (validated_data[text_attr] or "").strip() != current:
        validated_data[html_attr] = ""


def _coerce_product_form_data(data):
    """Normalize QueryDict/multipart payloads before serializer validation."""
    if hasattr(data, "dict"):
        mutable = data.dict()
    elif isinstance(data, dict):
        mutable = data.copy()
    elif hasattr(data, "get"):
        mutable = dict(data)
    else:
        return data

    if mutable.get("subcategoryId") == "":
        mutable["subcategoryId"] = None
    for price_key in ("price", "priceMax"):
        if mutable.get(price_key) == "":
            mutable[price_key] = None
    mutable["technicalAttributes"] = _parse_json_form_list(
        mutable.get("technicalAttributes")
    )
    mutable["searchTagIds"] = _parse_json_form_list(mutable.get("searchTagIds"))
    mutable["customKeywords"] = _parse_json_form_list(mutable.get("customKeywords"))
    mutable["targetMarkets"] = _parse_json_form_list(mutable.get("targetMarkets"))
    mutable["keepImageIds"] = _parse_json_form_list(mutable.get("keepImageIds"))
    if mutable.get("primaryImageId") == "":
        mutable["primaryImageId"] = None
    return mutable


def _attribute_value_from_input(attribute, value):
    value = (value or "").strip()
    if attribute.attribute_type == "number":
        if not value:
            return {"num_value": None, "txt_value": "", "bool_value": False}
        try:
            return {
                "num_value": float(value.replace(",", ".")),
                "txt_value": "",
                "bool_value": False,
            }
        except ValueError:
            return {"num_value": None, "txt_value": value, "bool_value": False}
    if attribute.attribute_type == "boolean":
        return {
            "num_value": None,
            "txt_value": "",
            "bool_value": value.lower() in ("true", "1", "yes", "oui"),
        }
    return {"num_value": None, "txt_value": value, "bool_value": False}


MAX_VALUES_PER_ATTRIBUTE = 20


def _save_product_technical_attributes(product, category, subcategory, items):
    product.attribute_values.all().delete()
    saved: dict[int, set] = {}
    for item in items or []:
        attribute_id = item.get("attributeId")
        value = item.get("value")
        if not attribute_id:
            continue
        try:
            attribute = TechnicalAttribute.objects.get(pk=attribute_id)
        except TechnicalAttribute.DoesNotExist:
            continue
        if not _attribute_allowed_on_product(attribute, category, subcategory):
            continue
        parsed = _attribute_value_from_input(attribute, value)
        if (
            parsed["num_value"] is None
            and not parsed["txt_value"]
            and attribute.attribute_type != "boolean"
        ):
            continue
        keys = saved.setdefault(attribute.id, set())
        key = (parsed["num_value"], parsed["txt_value"].casefold(), parsed["bool_value"])
        if key in keys:
            continue
        if keys and not attribute.allows_multiple_values:
            continue
        if len(keys) >= MAX_VALUES_PER_ATTRIBUTE:
            continue
        keys.add(key)
        ProductAttributeValue.objects.create(
            product=product,
            attribute=attribute,
            **parsed,
        )


def _join_attribute_values(values) -> str | None:
    """Display text for one attribute's value rows: '20µm / 30µm'."""
    seen = []
    for av in values:
        display = _attribute_display_value(av)
        if display and display not in seen:
            seen.append(display)
    return " / ".join(seen) or None


def _group_attribute_values(values):
    """Value rows grouped by attribute, in first-appearance order."""
    groups = {}
    for av in values:
        groups.setdefault(av.attribute_id, []).append(av)
    return list(groups.values())


def _read_product_attribute_values(product):
    rows = []
    for av in product.attribute_values.select_related("attribute").all():
        if av.num_value is not None:
            value = str(av.num_value)
        elif av.txt_value:
            value = av.txt_value
        else:
            value = "true" if av.bool_value else "false"
        rows.append({"attributeId": av.attribute_id, "value": value})
    return rows


def _product_technical_sheet_rows(product):
    rows = [
        {"label": "MOQ", "value": _product_moq_label(product)},
        {"label": "Livraison", "value": _product_delivery_label(product)},
    ]
    dimensions = _product_dimensions(product)
    if dimensions:
        rows.append({"label": "Dimensions", "value": dimensions})
    if product.material_id:
        rows.append(
            {
                "label": "Matière première",
                "value": product.material.name_fr or product.material.name_en,
            }
        )

    dim_keys = {"moq", "livraison", "dimensions", "matière première"}
    values = product.attribute_values.select_related("attribute", "attribute__unit").all()
    for group in _group_attribute_values(values):
        av = group[0]
        label = (av.attribute.name_fr or av.attribute.name_en or "").strip()
        if not label:
            continue
        if label.lower() in dim_keys:
            continue
        if av.attribute.attribute_type == "boolean":
            value = "Oui" if av.bool_value else "Non"
        else:
            value = _join_attribute_values(group)
        if value:
            rows.append({"label": label, "value": value})
    return rows


def _serialize_product_image(image, request):
    url = image.image.url
    if request is not None:
        url = request.build_absolute_uri(url)
    return {
        "id": image.id,
        "url": url,
        "isPrimary": image.is_primary,
    }


def _get_uploaded_product_images(context):
    request = context.get("request")
    if not request:
        return []
    files = list(request.FILES.getlist("images"))
    single = request.FILES.get("image")
    if single and single not in files:
        files.insert(0, single)
    return files


def _sync_product_images(product, context, keep_ids=None, primary_id=None, primary_new_index=None):
    """Replace product gallery according to keepImageIds + new uploads."""
    from user.product_photos import PRODUCT_IMAGE_MAX, bill_new_product_extra_photos

    limit = context.get("image_limit", PRODUCT_IMAGE_MAX)
    new_files = _get_uploaded_product_images(context)
    supplier = context.get("supplier")
    request = context.get("request")
    actor = request.user if request and getattr(request.user, "is_authenticated", False) else None
    old_count = product.images.count()

    if keep_ids is None:
        keep_ids = list(product.images.values_list("id", flat=True))

    keep_ids = [int(i) for i in keep_ids if i]
    product.images.exclude(id__in=keep_ids).delete()

    remaining = product.images.filter(id__in=keep_ids).count()
    if remaining + len(new_files) > limit:
        raise serializers.ValidationError(
            {
                "images": f"Maximum {limit} photo(s) par produit.",
            }
        )

    created_ids = []
    for uploaded in new_files:
        img = ProductImage.objects.create(product=product, image=uploaded)
        created_ids.append(img.id)

    chosen_primary = None
    if primary_id:
        try:
            chosen_primary = int(primary_id)
        except (TypeError, ValueError):
            chosen_primary = None

    if chosen_primary and product.images.filter(id=chosen_primary).exists():
        product.images.update(is_primary=False)
        product.images.filter(id=chosen_primary).update(is_primary=True)
    elif primary_new_index is not None and created_ids:
        try:
            idx = int(primary_new_index)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(created_ids):
            product.images.update(is_primary=False)
            ProductImage.objects.filter(pk=created_ids[idx]).update(is_primary=True)
    elif not product.images.filter(is_primary=True).exists():
        first = product.images.order_by("id").first()
        if first:
            product.images.update(is_primary=False)
            ProductImage.objects.filter(pk=first.pk).update(is_primary=True)

    if supplier:
        new_count = product.images.count()
        bill_new_product_extra_photos(
            supplier,
            product,
            old_count=old_count,
            new_count=new_count,
            actor=actor,
        )


def _save_product_primary_image(product, image_file, context=None):
    """Legacy single-image upload — replaces entire gallery with one image."""
    if not image_file:
        return
    from user.product_photos import bill_new_product_extra_photos

    ctx = context or {}
    supplier = ctx.get("supplier")
    request = ctx.get("request")
    actor = request.user if request and getattr(request.user, "is_authenticated", False) else None
    old_count = product.images.count()
    product.images.all().delete()
    ProductImage.objects.create(
        product=product,
        image=image_file,
        is_primary=True,
    )
    if supplier:
        bill_new_product_extra_photos(
            supplier,
            product,
            old_count=old_count,
            new_count=product.images.count(),
            actor=actor,
        )


def _serialize_search_tag(tag):
    return {
        "id": tag.id,
        "tag": tag.tag,
        "tagFr": tag.tag_fr,
        "tagAr": tag.tag_ar,
        "label": tag.tag_fr or tag.tag,
    }


def _apply_product_search_tags(product, tag_ids):
    apply_product_search_tags(product, tag_ids)


class CategorySerializer(serializers.ModelSerializer):
    nameEn = serializers.CharField(source="name_en")
    nameFr = serializers.CharField(source="name_fr")
    plainDescription = serializers.CharField(source="plain_description")
    sortOrder = serializers.IntegerField(source="sort_order")
    isNewInV2 = serializers.BooleanField(source="is_new_in_v2")
    isActive = serializers.SerializerMethodField()
    isService = serializers.SerializerMethodField()
    # Taxonomy v3 aliases (Famille). Legacy subcategoryCount kept for clients.
    productTypeCount = serializers.SerializerMethodField()
    productCount = serializers.SerializerMethodField()
    icon = serializers.SerializerMethodField()
    imageUrl = serializers.SerializerMethodField()
    subcategoryCount = serializers.SerializerMethodField()
    supplierCount = serializers.SerializerMethodField()
    technicalAttributeCount = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "slug",
            "nameEn",
            "nameFr",
            "plainDescription",
            "sortOrder",
            "isNewInV2",
            "isActive",
            "isService",
            "productCount",
            "icon",
            "imageUrl",
            "subcategoryCount",
            "productTypeCount",
            "supplierCount",
            "technicalAttributeCount",
        ]

    def get_productCount(self, obj):
        if hasattr(obj, "active_product_count"):
            return obj.active_product_count
        if obj.parent_id is None:
            return Product.objects.filter(category=obj, is_active=True).count()
        return Product.objects.filter(subcategory=obj, is_active=True).count()

    def get_icon(self, obj):
        return None

    def get_imageUrl(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        url = obj.image.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def get_subcategoryCount(self, obj):
        if hasattr(obj, "subcategory_count"):
            return obj.subcategory_count
        from user.category_tree import count_selectable_subcategories

        if obj.parent_id is None:
            return count_selectable_subcategories(obj)
        from user.taxonomy_v3 import ACTIVE_TYPE_SLUGS

        return obj.children.filter(slug__in=ACTIVE_TYPE_SLUGS).count()

    def get_productTypeCount(self, obj):
        return self.get_subcategoryCount(obj)

    def get_isActive(self, obj):
        from user.taxonomy_v3 import is_nav_family, is_nav_type

        return is_nav_family(obj) if obj.parent_id is None else is_nav_type(obj)

    def get_isService(self, obj):
        from user.taxonomy_v3 import is_service_category

        return is_service_category(obj)

    def get_supplierCount(self, obj):
        if hasattr(obj, "supplier_count"):
            return obj.supplier_count
        return (
            Supplier.objects.filter(
                products__category=obj,
                products__is_active=True,
                is_active=True,
                is_ghost=False,
            )
            .distinct()
            .count()
        )

    def get_technicalAttributeCount(self, obj):
        if hasattr(obj, "attribute_count"):
            return obj.attribute_count
        return obj.category_attributes.count()


class SubCategorySerializer(serializers.ModelSerializer):
    nameEn = serializers.CharField(source="name_en")
    nameFr = serializers.CharField(source="name_fr")
    sortOrder = serializers.IntegerField(source="sort_order")
    isActive = serializers.SerializerMethodField()
    isService = serializers.SerializerMethodField()
    parentSlug = serializers.CharField(source="parent.slug", read_only=True)
    parentNameEn = serializers.CharField(source="parent.name_en", read_only=True)
    # Taxonomy v3 aliases (Type de produit / Famille).
    familyId = serializers.SerializerMethodField()
    familyNameFr = serializers.SerializerMethodField()
    categoryId = serializers.SerializerMethodField()
    categoryNameEn = serializers.SerializerMethodField()
    productCount = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "slug",
            "nameEn",
            "nameFr",
            "sortOrder",
            "isActive",
            "isService",
            "parentSlug",
            "parentNameEn",
            "familyId",
            "familyNameFr",
            "categoryId",
            "categoryNameEn",
            "productCount",
        ]

    def get_categoryId(self, obj):
        from user.category_tree import root_category_for

        root = root_category_for(obj)
        return root.id if root else obj.parent_id

    def get_familyId(self, obj):
        return self.get_categoryId(obj)

    def get_familyNameFr(self, obj):
        from user.category_tree import root_category_for

        root = root_category_for(obj)
        if root:
            return root.name_fr or root.name_en
        if obj.parent_id:
            return obj.parent.name_fr or obj.parent.name_en
        return None

    def get_categoryNameEn(self, obj):
        from user.category_tree import root_category_for

        root = root_category_for(obj)
        if root:
            return root.name_en
        return obj.parent.name_en if obj.parent_id else None

    def get_productCount(self, obj):
        if hasattr(obj, "active_product_count"):
            return obj.active_product_count
        return Product.objects.filter(subcategory=obj, is_active=True).count()

    def get_isActive(self, obj):
        from user.taxonomy_v3 import is_nav_type

        return is_nav_type(obj)

    def get_isService(self, obj):
        from user.taxonomy_v3 import is_service_category

        return is_service_category(obj)


class SupplierListSerializer(serializers.ModelSerializer):
    companyName = serializers.CharField(source="company_name")
    tagline = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()
    country = serializers.SerializerMethodField()
    flag = serializers.SerializerMethodField()
    isVerified = serializers.SerializerMethodField()
    isFeatured = serializers.BooleanField(source="is_featured")
    rating = serializers.SerializerMethodField()
    reviewCount = serializers.SerializerMethodField()
    yearsInBusiness = serializers.SerializerMethodField()
    categories = serializers.SerializerMethodField()
    subcategories = serializers.SerializerMethodField()
    productCount = serializers.SerializerMethodField()
    orderCount = serializers.SerializerMethodField()
    description = serializers.CharField(source="presentation", allow_null=True)
    brandColor = serializers.CharField(source="brand_color", allow_null=True)
    brandButtonColor = serializers.CharField(
        source="brand_button_color", allow_null=True
    )
    brandNeutralColor = serializers.CharField(
        source="brand_neutral_color", allow_null=True
    )
    logoEmoji = serializers.SerializerMethodField()
    coverColor = serializers.SerializerMethodField()
    searchBoutiqueDesign = serializers.SerializerMethodField()
    responseTime = serializers.SerializerMethodField()
    logo = serializers.SerializerMethodField()
    website = serializers.URLField(allow_null=True)

    class Meta:
        model = Supplier
        fields = [
            "id",
            "companyName",
            "tagline",
            "location",
            "country",
            "flag",
            "isVerified",
            "isFeatured",
            "rating",
            "reviewCount",
            "yearsInBusiness",
            "categories",
            "subcategories",
            "productCount",
            "orderCount",
            "description",
            "brandColor",
            "brandButtonColor",
            "brandNeutralColor",
            "logo",
            "logoEmoji",
            "coverColor",
            "searchBoutiqueDesign",
            "responseTime",
            "website",
        ]

    def get_tagline(self, obj):
        if obj.type:
            return obj.get_type_display()
        return None

    def get_location(self, obj):
        return obj.get_wilaya_display() if obj.wilaya else (obj.address or "")

    def get_country(self, obj):
        return "Algeria"

    def get_flag(self, obj):
        return "🇩🇿"

    def get_isVerified(self, obj):
        return obj.is_active

    def get_rating(self, obj):
        average, _count = delivery_review_stats(obj)
        return average

    def get_reviewCount(self, obj):
        _average, count = delivery_review_stats(obj)
        return count

    def get_yearsInBusiness(self, obj):
        from datetime import date

        return max(date.today().year - obj.created_at.year, 1)

    def get_categories(self, obj):
        names = [
            n.strip()
            for n in obj.interested_categories.values_list("name_fr", flat=True).order_by(
                "sort_order", "name_fr"
            )
            if n and str(n).strip()
        ]
        if not names:
            names = [
                n.strip()
                for n in obj.interested_categories.values_list("name_en", flat=True).order_by(
                    "sort_order", "name_en"
                )
                if n and str(n).strip()
            ]
        if not names:
            from user.category_tree import root_category_for

            seen_root_ids: set[int] = set()
            for sub in obj.interested_subcategories.select_related(
                "parent", "parent__parent"
            ).order_by("sort_order", "name_fr"):
                root = root_category_for(sub)
                if root is None or root.id in seen_root_ids:
                    continue
                seen_root_ids.add(root.id)
                label = (root.name_fr or "").strip() or (root.name_en or "").strip()
                if label:
                    names.append(label)
        if names:
            return names
        return list(
            obj.products.filter(is_active=True)
            .values_list("category__name_fr", flat=True)
            .distinct()[:5]
        ) or list(
            obj.products.filter(is_active=True)
            .values_list("category__name_en", flat=True)
            .distinct()[:5]
        )

    def get_subcategories(self, obj):
        return list(
            obj.interested_subcategories.values_list('name_fr', flat=True).order_by(
                'sort_order', 'name_fr'
            )
        ) or list(
            obj.interested_subcategories.values_list('name_en', flat=True).order_by(
                'sort_order', 'name_en'
            )
        )

    def get_productCount(self, obj):
        if hasattr(obj, "active_product_count"):
            return obj.active_product_count
        return obj.products.filter(is_active=True).count()

    def get_orderCount(self, obj):
        if hasattr(obj, "order_count"):
            return obj.order_count
        return Order.objects.filter(supplier=obj).count()

    def get_logo(self, obj):
        if not obj.logo:
            return None
        request = self.context.get("request")
        url = obj.logo.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def get_logoEmoji(self, obj):
        return "🏭"

    def get_coverColor(self, obj):
        if obj.brand_color:
            return obj.brand_color
        return "#e8642c"

    def get_searchBoutiqueDesign(self, obj):
        return _supplier_search_boutique_design(obj)

    def get_responseTime(self, obj):
        return None


class SupplierProductSerializer(serializers.ModelSerializer):
    supplierName = serializers.CharField(source="supplier.company_name", read_only=True)
    emoji = serializers.SerializerMethodField()
    moq = serializers.SerializerMethodField()
    leadTimeDays = serializers.IntegerField(source="lead_time_days")
    listingType = serializers.CharField(source="listing_type", read_only=True)
    priceFrom = serializers.SerializerMethodField()
    priceBadge = serializers.SerializerMethodField()
    delivery = serializers.SerializerMethodField()
    dimensions = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    searchTags = serializers.SerializerMethodField()
    imageUrl = serializers.SerializerMethodField()
    secondaryImageUrl = serializers.SerializerMethodField()
    completenessPct = serializers.SerializerMethodField()
    subcategoryId = serializers.IntegerField(
        source="subcategory_id", allow_null=True, read_only=True
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "supplierName",
            "emoji",
            "moq",
            "leadTimeDays",
            "listingType",
            "priceFrom",
            "priceBadge",
            "delivery",
            "dimensions",
            "tags",
            "searchTags",
            "imageUrl",
            "secondaryImageUrl",
            "completenessPct",
            "subcategoryId",
        ]

    def get_emoji(self, obj):
        return "📦"

    def get_moq(self, obj):
        return _product_moq_label(obj)

    def get_priceFrom(self, obj):
        return _product_price_badge(obj)

    def get_priceBadge(self, obj):
        return _product_price_badge(obj)

    def get_delivery(self, obj):
        return _product_delivery_label(obj)

    def get_dimensions(self, obj):
        return _product_dimensions(obj)

    def get_tags(self, obj):
        tags = list(target_market_labels(obj.target_markets)[:3])
        if obj.material:
            tags.append(obj.material.name_en)
        if obj.category:
            tags.append(obj.category.name_en)
        for search_tag in obj.search_tags.all()[:3]:
            tags.append(search_tag.tag_fr or search_tag.tag)
        return tags[:5]

    def get_searchTags(self, obj):
        return [
            {
                "en": tag.tag,
                "fr": tag.tag_fr,
                "ar": tag.tag_ar,
            }
            for tag in obj.search_tags.all()
        ]

    def get_imageUrl(self, obj):
        return _product_image_url(obj, self.context.get("request"))

    def get_secondaryImageUrl(self, obj):
        return _product_secondary_image_url(obj, self.context.get("request"))

    def get_completenessPct(self, obj):
        return calculate_product_completeness(obj)


class SearchTagSerializer(serializers.ModelSerializer):
    tagFr = serializers.CharField(source="tag_fr")
    tagAr = serializers.CharField(source="tag_ar")
    label = serializers.SerializerMethodField()

    class Meta:
        model = SearchTag
        fields = ["id", "tag", "tagFr", "tagAr", "label"]

    def get_label(self, obj):
        return obj.tag_fr or obj.tag


class SupplierProductFormSerializer(serializers.ModelSerializer):
    categoryId = serializers.PrimaryKeyRelatedField(
        source="category",
        queryset=Category.objects.filter(parent__isnull=True),
    )
    subcategoryId = serializers.PrimaryKeyRelatedField(
        source="subcategory",
        queryset=Category.objects.filter(parent__isnull=False),
        allow_null=True,
        required=False,
    )
    minOrderQuantity = serializers.IntegerField(
        source="min_order_quantity", min_value=1, default=1
    )
    leadTimeDays = serializers.IntegerField(
        source="lead_time_days", min_value=0, default=14
    )
    listingType = serializers.ChoiceField(
        source="listing_type",
        choices=Product.ListingType.choices,
        default=Product.ListingType.SELL,
    )
    technicalAttributes = serializers.JSONField(required=False, write_only=True, default=list)
    searchTagIds = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        required=False,
        default=list,
    )
    customKeywords = serializers.ListField(
        child=serializers.CharField(max_length=CUSTOM_KEYWORD_MAX_LENGTH, allow_blank=False),
        write_only=True,
        required=False,
        default=list,
    )
    targetMarkets = serializers.ListField(
        child=serializers.CharField(max_length=32),
        source="target_markets",
        required=False,
        default=list,
    )
    searchTags = serializers.SerializerMethodField()
    searchTagLimit = serializers.SerializerMethodField()
    imageLimit = serializers.SerializerMethodField()
    subscriptionTier = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    image = serializers.ImageField(write_only=True, required=False, allow_null=True)
    keepImageIds = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        required=False,
    )
    primaryImageId = serializers.IntegerField(
        write_only=True, required=False, allow_null=True
    )
    primaryNewIndex = serializers.IntegerField(
        write_only=True, required=False, allow_null=True
    )
    attributeValues = serializers.SerializerMethodField()
    imageUrl = serializers.SerializerMethodField()
    completenessPct = serializers.SerializerMethodField()
    descriptionHtml = serializers.CharField(
        source="description_html",
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        max_length=RICH_HTML_MAX_LENGTH,
    )
    priceMax = serializers.DecimalField(
        source="price_max",
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "descriptionHtml",
            "categoryId",
            "subcategoryId",
            "minOrderQuantity",
            "price",
            "priceMax",
            "leadTimeDays",
            "listingType",
            "technicalAttributes",
            "searchTagIds",
            "customKeywords",
            "targetMarkets",
            "searchTags",
            "searchTagLimit",
            "imageLimit",
            "subscriptionTier",
            "images",
            "image",
            "keepImageIds",
            "primaryImageId",
            "primaryNewIndex",
            "attributeValues",
            "imageUrl",
            "completenessPct",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "description": {"required": False, "allow_blank": True},
            "price": {"required": False, "allow_null": True, "min_value": Decimal("0")},
        }

    def get_attributeValues(self, obj):
        return _read_product_attribute_values(obj)

    def _validate_price_range(self, attrs):
        """A range needs a lower bound strictly below the upper bound."""
        price = attrs["price"] if "price" in attrs else getattr(self.instance, "price", None)
        if "price" in attrs and attrs["price"] is None and "price_max" not in attrs:
            attrs["price_max"] = None
        price_max = (
            attrs["price_max"] if "price_max" in attrs else getattr(self.instance, "price_max", None)
        )
        if price_max is None:
            return
        if price is None:
            raise serializers.ValidationError(
                {"price": "Indiquez le prix minimum de l'intervalle."}
            )
        if price_max <= price:
            raise serializers.ValidationError(
                {"priceMax": "Le prix maximum doit être supérieur au prix minimum."}
            )

    def get_imageUrl(self, obj):
        return _product_image_url(obj, self.context.get("request"))

    def get_images(self, obj):
        request = self.context.get("request")
        return [
            _serialize_product_image(img, request)
            for img in obj.images.all().order_by("-is_primary", "id")
        ]

    def get_searchTags(self, obj):
        return [_serialize_search_tag(tag) for tag in obj.search_tags.all()]

    def get_searchTagLimit(self, obj):
        return self.context.get(
            "search_tag_limit", SEARCH_TAG_LIMITS["classic"]
        )

    def get_imageLimit(self, obj):
        return self.context.get(
            "image_limit", PRODUCT_IMAGE_LIMITS["classic"]
        )

    def get_subscriptionTier(self, obj):
        supplier = self.context.get("supplier")
        if supplier:
            return get_supplier_subscription_tier(supplier)
        return "classic"

    def get_completenessPct(self, obj):
        return calculate_product_completeness(obj)

    def validate_searchTagIds(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Les mots-clés ne doivent pas être dupliqués.")
        if value:
            found = SearchTag.objects.filter(id__in=value).count()
            if found != len(value):
                raise serializers.ValidationError(
                    "Un ou plusieurs mots-clés sont invalides."
                )
        return value

    def validate_customKeywords(self, value):
        return _normalize_custom_keywords(value)

    def validate_targetMarkets(self, value):
        return normalize_target_markets(value)

    def validate(self, attrs):
        category = attrs.get("category") or getattr(self.instance, "category", None)
        subcategory = attrs.get("subcategory")
        if category and subcategory and not subcategory_belongs_to_category(category, subcategory):
            raise serializers.ValidationError(
                {"subcategoryId": "La sous-catégorie doit appartenir à la catégorie."}
            )
        self._validate_price_range(attrs)

        limit = self.context.get(
            "search_tag_limit", SEARCH_TAG_LIMITS["classic"]
        )
        if self.instance is None:
            tag_ids = attrs.get("searchTagIds", [])
            custom = attrs.get("customKeywords", [])
        else:
            tag_ids = (
                attrs["searchTagIds"]
                if "searchTagIds" in attrs
                else list(self.instance.search_tags.values_list("id", flat=True))
            )
            custom = (
                attrs["customKeywords"]
                if "customKeywords" in attrs
                else (self.instance.custom_keywords or [])
            )
        resolved_tag_ids = resolve_search_tag_ids(tag_ids, custom)
        _validate_product_keyword_totals(resolved_tag_ids, limit)
        self._resolved_search_tag_ids = resolved_tag_ids
        return attrs

    def to_internal_value(self, data):
        if hasattr(data, "get"):
            data = _coerce_product_form_data(data)
        return super().to_internal_value(data)

    def to_representation(self, instance):
        if instance.custom_keywords:
            promote_product_custom_keywords(instance)
            instance.refresh_from_db(fields=["custom_keywords"])
        data = super().to_representation(instance)
        data["customKeywords"] = []
        data["targetMarkets"] = normalize_target_markets(instance.target_markets)
        return data

    @transaction.atomic
    def create(self, validated_data):
        technical_attributes = validated_data.pop("technicalAttributes", [])
        resolved_tag_ids = getattr(self, "_resolved_search_tag_ids", [])
        validated_data.pop("searchTagIds", None)
        validated_data.pop("customKeywords", None)
        image = validated_data.pop("image", None)
        keep_image_ids = validated_data.pop("keepImageIds", None)
        primary_image_id = validated_data.pop("primaryImageId", None)
        primary_new_index = validated_data.pop("primaryNewIndex", None)
        supplier_id = self.context["supplier_id"]
        category = validated_data["category"]
        subcategory = validated_data.get("subcategory")
        if not validated_data.get("description"):
            validated_data["description"] = validated_data.get("name", "")
        validated_data["custom_keywords"] = []
        product = Product.objects.create(
            supplier_id=supplier_id,
            **validated_data,
        )
        _save_product_technical_attributes(
            product, category, subcategory, technical_attributes
        )
        uploaded = _get_uploaded_product_images(self.context)
        if uploaded or keep_image_ids is not None:
            _sync_product_images(
                product,
                self.context,
                keep_ids=keep_image_ids or [],
                primary_id=primary_image_id,
                primary_new_index=primary_new_index,
            )
        elif image:
            _save_product_primary_image(product, image, self.context)
        _apply_product_search_tags(product, resolved_tag_ids)
        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        technical_attributes = validated_data.pop("technicalAttributes", None)
        resolved_tag_ids = getattr(self, "_resolved_search_tag_ids", None)
        validated_data.pop("searchTagIds", None)
        validated_data.pop("customKeywords", None)
        image = validated_data.pop("image", None)
        keep_image_ids = validated_data.pop("keepImageIds", None)
        primary_image_id = validated_data.pop("primaryImageId", None)
        primary_new_index = validated_data.pop("primaryNewIndex", None)
        category = validated_data.get("category", instance.category)
        subcategory = validated_data.get("subcategory", instance.subcategory)
        validated_data["custom_keywords"] = []
        _drop_stale_rich_html(instance, validated_data, "description_html", "description")
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if technical_attributes is not None:
            _save_product_technical_attributes(
                instance, category, subcategory, technical_attributes
            )
        uploaded = _get_uploaded_product_images(self.context)
        if keep_image_ids is not None or uploaded:
            _sync_product_images(
                instance,
                self.context,
                keep_ids=keep_image_ids,
                primary_id=primary_image_id,
                primary_new_index=primary_new_index,
            )
        elif image:
            _save_product_primary_image(instance, image, self.context)
        if resolved_tag_ids is not None:
            _apply_product_search_tags(instance, resolved_tag_ids)
        return instance


class SupplierDetailSerializer(SupplierListSerializer):
    tagline = serializers.SerializerMethodField()
    typeLabel = serializers.SerializerMethodField()
    brandButtonColor = serializers.CharField(
        source="brand_button_color", allow_null=True
    )
    brandNeutralColor = serializers.CharField(
        source="brand_neutral_color", allow_null=True
    )
    founded = serializers.SerializerMethodField()
    employees = serializers.SerializerMethodField()
    about = serializers.CharField(source="presentation", allow_blank=True)
    aboutHtml = serializers.CharField(source="presentation_html", read_only=True)
    products = SupplierProductSerializer(many=True, source="active_products")
    email = serializers.EmailField()
    phone = serializers.CharField(source="whatsapp_number", allow_null=True)
    website = serializers.URLField(allow_null=True)
    instagramUrl = serializers.URLField(source="instagram_url", allow_null=True)
    facebookUrl = serializers.URLField(source="facebook_url", allow_null=True)
    linkedinUrl = serializers.URLField(source="linkedin_url", allow_null=True)
    certifications = serializers.SerializerMethodField()
    industries = serializers.SerializerMethodField()
    stats = serializers.SerializerMethodField()
    faq = serializers.SerializerMethodField()
    productCardStyle = serializers.SerializerMethodField()
    listingCardFields = serializers.SerializerMethodField()
    pageDesign = serializers.SerializerMethodField()
    quoteCategories = serializers.SerializerMethodField()

    class Meta(SupplierListSerializer.Meta):
        fields = [
            "id",
            "companyName",
            "tagline",
            "type",
            "typeLabel",
            "location",
            "country",
            "flag",
            "founded",
            "employees",
            "logo",
            "logoEmoji",
            "coverColor",
            "brandColor",
            "brandButtonColor",
            "brandNeutralColor",
            "isVerified",
            "rating",
            "reviewCount",
            "responseTime",
            "categories",
            "subcategories",
            "certifications",
            "industries",
            "about",
            "aboutHtml",
            "products",
            "email",
            "phone",
            "website",
            "instagramUrl",
            "facebookUrl",
            "linkedinUrl",
            "stats",
            "faq",
            "productCardStyle",
            "listingCardFields",
            "pageDesign",
            "quoteCategories",
        ]

    def get_quoteCategories(self, obj):
        from user.quote_forms import offered_category_payloads

        return offered_category_payloads(obj)

    def get_pageDesign(self, obj):
        settings_obj = getattr(obj, "settings", None)
        if settings_obj is None:
            return {
                "storeHeaderTier": "standard",
                "showStats": "true",
                "showRating": "true",
                "coverColor": obj.brand_color or "",
                "useStoreHeaderInSearch": "false",
                "samplesEnabled": "true",
                "sampleQuantity": str(DEFAULT_SAMPLE_QUANTITY),
            }
        raw = settings_obj.page_design or {}
        samples = sample_settings(obj)
        tier = str(raw.get("storeHeaderTier") or "standard").lower()
        if tier not in {"standard", "extra", "central", "mega"}:
            tier = "standard"
        # Tiers payants autorisés pour tous (phase test boutique).
        cover = (raw.get("coverColor") or obj.brand_color or "").strip()
        use_in_search = str(raw.get("useStoreHeaderInSearch", "")).lower() in (
            "true",
            "1",
            "yes",
        )
        if tier not in {"extra", "central", "mega"}:
            use_in_search = False
        return {
            "storeHeaderTier": tier,
            "showStats": str(raw.get("showStats", "true")),
            "showRating": str(raw.get("showRating", "true")),
            "coverColor": cover,
            "accentColor": str(raw.get("accentColor") or ""),
            "useStoreHeaderInSearch": "true" if use_in_search else "false",
            "logoZoom": str(raw.get("logoZoom") or "1"),
            "logoOffsetX": str(raw.get("logoOffsetX") or "0"),
            "logoOffsetY": str(raw.get("logoOffsetY") or "0"),
            "samplesEnabled": "true" if samples["enabled"] else "false",
            "sampleQuantity": str(samples["quantity"]),
        }

    def get_responseTime(self, obj):
        return format_response_time(supplier_response_minutes(obj.id))

    def get_tagline(self, obj):
        return obj.get_type_display() if obj.type else "Fournisseur d'emballages"

    def get_typeLabel(self, obj):
        return obj.get_type_display() if obj.type else None

    def get_founded(self, obj):
        return obj.created_at.year

    def get_employees(self, obj):
        return "—"

    def get_certifications(self, obj):
        return []

    def get_industries(self, obj):
        return self.get_categories(obj)

    def get_stats(self, obj):
        product_count = obj.products.filter(is_active=True).count()
        order_count = Order.objects.filter(supplier=obj).count()
        score_record = ensure_supplier_score(obj.id)
        rank, _ = get_supplier_rank(obj.id)
        return [
            {"label": "Score Paknomik", "value": str(score_record.score_total)},
            {"label": "Classement", "value": str(rank)},
            {"label": "Produit", "value": str(product_count)},
            {"label": "Commandes", "value": str(order_count)},
        ]

    def get_faq(self, obj):
        settings_obj = getattr(obj, "settings", None)
        if settings_obj is None:
            return []
        items = settings_obj.faq_items or []
        return [
            item
            for item in items
            if isinstance(item, dict)
            and (item.get("question") or "").strip()
            and (item.get("answer") or "").strip()
        ]

    def get_productCardStyle(self, obj):
        return get_supplier_product_card_style(obj)

    def get_listingCardFields(self, obj):
        settings_obj = getattr(obj, "settings", None)
        if settings_obj is None:
            return []
        return settings_obj.listing_card_fields or []


class ProductListSerializer(serializers.ModelSerializer):
    supplierId = serializers.IntegerField(source="supplier_id")
    supplierName = serializers.CharField(source="supplier.company_name")
    location = serializers.SerializerMethodField()
    category = serializers.CharField(source="category.name_en")
    moq = serializers.SerializerMethodField()
    leadTimeDays = serializers.IntegerField(source="lead_time_days")
    priceFrom = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()
    reviewCount = serializers.SerializerMethodField()
    isVerified = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    searchTags = serializers.SerializerMethodField()
    attributeValues = serializers.SerializerMethodField()
    dimensions = serializers.SerializerMethodField()
    imageUrl = serializers.SerializerMethodField()
    secondaryImageUrl = serializers.SerializerMethodField()
    emoji = serializers.SerializerMethodField()
    productCardStyle = serializers.SerializerMethodField()
    boutiqueSearchPriority = serializers.SerializerMethodField()
    supplierLogo = serializers.SerializerMethodField()
    subcategoryId = serializers.IntegerField(source="subcategory_id", allow_null=True, read_only=True)
    listingType = serializers.CharField(source="listing_type", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "listingType",
            "supplierId",
            "supplierName",
            "supplierLogo",
            "location",
            "category",
            "subcategoryId",
            "moq",
            "leadTimeDays",
            "priceFrom",
            "rating",
            "reviewCount",
            "isVerified",
            "tags",
            "searchTags",
            "attributeValues",
            "dimensions",
            "imageUrl",
            "secondaryImageUrl",
            "emoji",
            "productCardStyle",
            "boutiqueSearchPriority",
        ]

    def get_location(self, obj):
        if obj.supplier.wilaya:
            return obj.supplier.get_wilaya_display()
        return obj.supplier.address or ""

    def get_moq(self, obj):
        return f"{obj.min_order_quantity:,} units"

    def get_priceFrom(self, obj):
        return _product_price_badge(obj)

    def get_rating(self, obj):
        average, _count = delivery_review_stats(obj)
        return average

    def get_reviewCount(self, obj):
        _average, count = delivery_review_stats(obj)
        return count

    def get_isVerified(self, obj):
        return obj.supplier.is_active

    def get_tags(self, obj):
        tags = list(target_market_labels(obj.target_markets)[:3])
        if obj.material:
            tags.append(obj.material.name_en)
        if obj.is_featured:
            tags.append("Featured")
        for search_tag in obj.search_tags.all()[:3]:
            tags.append(search_tag.tag_fr or search_tag.tag)
        return tags

    def get_attributeValues(self, obj):
        return _read_product_attribute_values(obj)

    def get_dimensions(self, obj):
        return _product_dimensions(obj)

    def get_searchTags(self, obj):
        return [
            {
                "en": tag.tag,
                "fr": tag.tag_fr,
                "ar": tag.tag_ar,
            }
            for tag in obj.search_tags.all()
        ]

    def get_imageUrl(self, obj):
        image = obj.get_display_image()
        if not image:
            return None
        request = self.context.get("request")
        url = image.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def get_secondaryImageUrl(self, obj):
        return _product_secondary_image_url(obj, self.context.get("request"))

    def get_emoji(self, obj):
        return "📦"

    def get_productCardStyle(self, obj):
        return get_supplier_product_card_style_for_search(obj.supplier)

    def get_boutiqueSearchPriority(self, obj):
        return bool(_supplier_search_boutique_design(obj.supplier))

    def get_supplierLogo(self, obj):
        request = self.context.get("request")
        if obj.supplier.logo and request:
            return request.build_absolute_uri(obj.supplier.logo.url)
        if obj.supplier.logo:
            return obj.supplier.logo.url
        return None


class AttributeOptionSerializer(serializers.ModelSerializer):
    valueEn = serializers.CharField(source="value_en")
    valueFr = serializers.CharField(source="value_fr")

    class Meta:
        model = AttributeOption
        fields = ["id", "valueEn", "valueFr"]


class TechnicalAttributeSerializer(serializers.ModelSerializer):
    nameEn = serializers.CharField(source="name_en")
    nameFr = serializers.CharField(source="name_fr")
    attributeType = serializers.CharField(source="attribute_type")
    attributeLevel = serializers.IntegerField(source="attribute_level")
    unitSymbol = serializers.SerializerMethodField()
    isRequired = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()

    class Meta:
        model = TechnicalAttribute
        fields = [
            "id",
            "nameEn",
            "nameFr",
            "attributeType",
            "attributeLevel",
            "unitSymbol",
            "isRequired",
            "options",
        ]

    def get_unitSymbol(self, obj):
        return obj.unit.symbol if obj.unit_id else None

    def get_isRequired(self, obj):
        return False

    def get_options(self, obj):
        link = self.context.get("category_attribute")
        if not link:
            return []
        return AttributeOptionSerializer(link.options.all(), many=True).data


QUOTE_STATUS_LABELS = {
    "submitted": "Envoyée",
    "in_review": "En étude",
    "quoted": "Devis répondu",
    "accepted": "Acceptée",
    "rejected": "Refusée",
    "cancelled": "Annulée",
}


class OrderExecutionFieldsMixin(serializers.Serializer):
    """Commercial terms and execution tracking shared by buyer and supplier views."""

    unitPrice = serializers.DecimalField(
        source="unit_price", max_digits=10, decimal_places=2, read_only=True
    )
    specifications = serializers.CharField(read_only=True)
    commercialSnapshot = serializers.JSONField(source="commercial_snapshot", read_only=True)
    buyerPoReference = serializers.CharField(source="buyer_po_reference", read_only=True)
    billingAddress = serializers.CharField(source="billing_address", read_only=True)
    deliveryAddress = serializers.CharField(source="delivery_address", read_only=True)
    deliveryInstructions = serializers.CharField(
        source="delivery_instructions", read_only=True
    )
    receiverContact = serializers.CharField(source="receiver_contact", read_only=True)
    expectedDeliveryDate = serializers.DateField(
        source="expected_delivery_date", read_only=True
    )
    quoteProposalId = serializers.IntegerField(
        source="quote_proposal_id", read_only=True, allow_null=True
    )
    quoteDemandId = serializers.SerializerMethodField()
    statusHistory = serializers.SerializerMethodField()
    origin = serializers.CharField(read_only=True)
    originLabel = serializers.CharField(source="get_origin_display", read_only=True)
    tradeScope = serializers.CharField(source="trade_scope", read_only=True)
    deliveryWilaya = serializers.CharField(source="delivery_wilaya", read_only=True)
    destinationCountry = serializers.CharField(source="destination_country", read_only=True)
    destinationCity = serializers.CharField(source="destination_city", read_only=True)
    incoterm = serializers.CharField(read_only=True)
    buyerNote = serializers.CharField(source="buyer_note", read_only=True)

    EXECUTION_FIELDS = [
        "origin",
        "originLabel",
        "tradeScope",
        "deliveryWilaya",
        "destinationCountry",
        "destinationCity",
        "incoterm",
        "buyerNote",
        "unitPrice",
        "specifications",
        "commercialSnapshot",
        "buyerPoReference",
        "billingAddress",
        "deliveryAddress",
        "deliveryInstructions",
        "receiverContact",
        "expectedDeliveryDate",
        "quoteProposalId",
        "quoteDemandId",
        "statusHistory",
    ]

    def get_quoteDemandId(self, obj):
        proposal = obj.quote_proposal if obj.quote_proposal_id else None
        return proposal.demand_id if proposal else None

    def get_statusHistory(self, obj):
        return serialize_status_history(obj)


class SupplierOrderSerializer(OrderExecutionFieldsMixin, serializers.ModelSerializer):
    productName = serializers.CharField(source="product.name", read_only=True)
    variantId = serializers.IntegerField(source="variant_id", read_only=True, allow_null=True)
    variantLabel = serializers.SerializerMethodField()
    launchDate = serializers.SerializerMethodField()
    leadTimeDays = serializers.IntegerField(source="product.lead_time_days", read_only=True)
    statusLabel = serializers.SerializerMethodField()
    contactName = serializers.SerializerMethodField()
    contactPhone = serializers.SerializerMethodField()
    contactEmail = serializers.SerializerMethodField()
    contactLocation = serializers.SerializerMethodField()
    profilePicture = serializers.SerializerMethodField()
    buyerId = serializers.IntegerField(source="buyer_id", read_only=True, allow_null=True)
    hasResponse = serializers.SerializerMethodField()
    latestResponse = serializers.SerializerMethodField()
    allowedTransitions = serializers.SerializerMethodField()
    commission = serializers.SerializerMethodField()
    archived = serializers.SerializerMethodField()
    archivedAt = serializers.DateTimeField(source="supplier_archived_at", read_only=True)
    canArchive = serializers.SerializerMethodField()
    canReopen = serializers.SerializerMethodField()
    amountEditable = serializers.SerializerMethodField()
    productPriceRange = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "productName",
            "variantId",
            "variantLabel",
            "amountEditable",
            "productPriceRange",
            "launchDate",
            "quantity",
            "leadTimeDays",
            "status",
            "statusLabel",
            "contactName",
            "contactPhone",
            "contactEmail",
            "contactLocation",
            "profilePicture",
            "buyerId",
            "hasResponse",
            "latestResponse",
            "allowedTransitions",
            "commission",
            "archived",
            "archivedAt",
            "canArchive",
            "canReopen",
            "created_at",
            *OrderExecutionFieldsMixin.EXECUTION_FIELDS,
        ]

    def get_variantLabel(self, obj):
        from user.catalog_taxonomy import format_variant_axes

        variant = getattr(obj, "variant", None)
        if variant is None:
            return ""
        return format_variant_axes(variant.axes)

    def get_commission(self, obj):
        """Supplier-side only: the buyer serializer never exposes commissions."""
        from user.commission import serialize_commission

        commission = getattr(obj, "commission", None) if obj.is_paknomik_origin else None
        if commission is None:
            return None
        return serialize_commission(commission)

    def get_archived(self, obj):
        return obj.supplier_archived_at is not None

    def get_canArchive(self, obj):
        return obj.status in SUPPLIER_ARCHIVABLE

    def get_canReopen(self, obj):
        return obj.status in SUPPLIER_REOPEN_TRANSITIONS

    def get_amountEditable(self, obj):
        from user.order_workflow import order_amount_editable

        return order_amount_editable(obj)

    def get_productPriceRange(self, obj):
        product = obj.product
        if product.price is None and product.price_max is None:
            return None
        return {
            "min": str(product.price) if product.price is not None else None,
            "max": str(product.price_max) if product.price_max is not None else None,
        }

    def get_allowedTransitions(self, obj):
        return [
            {"status": value, "label": ORDER_STATUS_LABELS.get(value, value)}
            for value in supplier_allowed_transitions(obj)
        ]

    def get_hasResponse(self, obj):
        if hasattr(obj, "_prefetched_responses"):
            return bool(obj._prefetched_responses)
        return obj.responses.exists()

    def get_latestResponse(self, obj):
        response = None
        if hasattr(obj, "_prefetched_responses") and obj._prefetched_responses:
            response = obj._prefetched_responses[0]
        else:
            response = obj.responses.order_by("-created_at").first()
        if not response:
            return None
        return {
            "id": response.id,
            "templateName": response.proposal_template_name or "",
            "createdAt": response.created_at.strftime("%d/%m/%Y"),
            "createdAtIso": response.created_at.isoformat(),
            "status": response.status,
            "statusLabel": response.get_status_display(),
        }

    def get_launchDate(self, obj):
        return obj.created_at.strftime("%d/%m/%Y")

    def get_statusLabel(self, obj):
        return ORDER_STATUS_LABELS.get(obj.status, obj.status)

    def get_contactName(self, obj):
        if obj.buyer_id:
            return obj.buyer.get_full_name() or obj.buyer.username
        return obj.guest_name or "—"

    def get_contactPhone(self, obj):
        if obj.buyer_id:
            return obj.buyer.phone_number or "—"
        if obj.guest_phone:
            code = (obj.guest_phone_country_code or "").strip()
            return f"{code} {obj.guest_phone}".strip()
        return "—"

    def get_contactEmail(self, obj):
        if obj.buyer_id:
            return obj.buyer.email or "—"
        return "—"

    def get_contactLocation(self, obj):
        if obj.trade_scope == "export" and obj.destination_country:
            city = f" — {obj.destination_city}" if obj.destination_city else ""
            return f"Export : {obj.destination_country}{city}"
        if obj.delivery_wilaya:
            return obj.delivery_wilaya
        if obj.buyer_id:
            return obj.buyer.wilaya or obj.buyer.address or "—"
        return "—"

    def get_profilePicture(self, obj):
        if not obj.buyer_id:
            return None
        request = self.context.get("request")
        return user_avatar_url(request, obj.buyer)


def _request_photo_payload(obj, request) -> list[dict]:
    photos = []
    for photo in obj.photos.all():
        url = photo.image.url
        photos.append({"id": photo.id, "url": request.build_absolute_uri(url) if request else url})
    return photos


class SupplierSampleRequestSerializer(serializers.ModelSerializer):
    createdAt = serializers.SerializerMethodField()
    createdAtIso = serializers.DateTimeField(source="created_at", read_only=True)
    statusLabel = serializers.CharField(source="get_status_display", read_only=True)
    productId = serializers.IntegerField(source="product_id", read_only=True)
    productName = serializers.SerializerMethodField()
    deliveryAddress = serializers.CharField(source="delivery_address", read_only=True)
    buyerId = serializers.IntegerField(source="buyer_id", read_only=True)
    photos = serializers.SerializerMethodField()

    class Meta:
        model = SampleRequest
        fields = [
            "id",
            "name",
            "company",
            "email",
            "phone",
            "quantity",
            "deliveryAddress",
            "message",
            "productId",
            "productName",
            "buyerId",
            "status",
            "statusLabel",
            "photos",
            "createdAt",
            "createdAtIso",
        ]

    def get_createdAt(self, obj):
        return obj.created_at.strftime("%d/%m/%Y")

    def get_productName(self, obj):
        return obj.product.name if obj.product_id and obj.product else ""

    def get_photos(self, obj):
        return _request_photo_payload(obj, self.context.get("request"))


class BuyerSampleRequestSerializer(serializers.ModelSerializer):
    createdAt = serializers.SerializerMethodField()
    createdAtIso = serializers.DateTimeField(source="created_at", read_only=True)
    statusLabel = serializers.CharField(source="get_status_display", read_only=True)
    productId = serializers.IntegerField(source="product_id", read_only=True)
    productName = serializers.SerializerMethodField()
    supplierId = serializers.IntegerField(source="supplier_id", read_only=True)
    supplierName = serializers.CharField(source="supplier.company_name", read_only=True)
    supplierLogo = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()
    deliveryAddress = serializers.CharField(source="delivery_address", read_only=True)
    photos = serializers.SerializerMethodField()

    class Meta:
        model = SampleRequest
        fields = [
            "id",
            "quantity",
            "location",
            "deliveryAddress",
            "message",
            "productId",
            "productName",
            "supplierId",
            "supplierName",
            "supplierLogo",
            "status",
            "statusLabel",
            "photos",
            "createdAt",
            "createdAtIso",
        ]

    def get_createdAt(self, obj):
        return obj.created_at.strftime("%d/%m/%Y")

    def get_productName(self, obj):
        return obj.product.name if obj.product_id and obj.product else ""

    def get_location(self, obj):
        address = (obj.delivery_address or "").strip()
        if address:
            return address.split(",")[0].strip()[:80]
        if obj.supplier.wilaya:
            return obj.supplier.get_wilaya_display()
        return ""

    def get_supplierLogo(self, obj):
        request = self.context.get("request")
        logo = obj.supplier.logo
        if not logo:
            return None
        return request.build_absolute_uri(logo.url) if request else logo.url

    def get_photos(self, obj):
        return _request_photo_payload(obj, self.context.get("request"))


class SupplierRfqSerializer(serializers.ModelSerializer):
    createdAt = serializers.SerializerMethodField()
    statusLabel = serializers.SerializerMethodField()
    title = serializers.CharField(read_only=True)
    productName = serializers.SerializerMethodField()
    quantityEstimate = serializers.IntegerField(source="quantity_estimate", read_only=True)
    hasResponse = serializers.SerializerMethodField()
    latestResponse = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    files = serializers.SerializerMethodField()
    quantityUnit = serializers.CharField(source="quantity_unit", read_only=True)
    categoryName = serializers.SerializerMethodField()
    specs = serializers.SerializerMethodField()
    physicalCustomization = serializers.SerializerMethodField()

    class Meta:
        model = RFQRequest
        fields = [
            "id",
            "name",
            "company",
            "email",
            "phone",
            "quantityEstimate",
            "quantityUnit",
            "destination",
            "message",
            "title",
            "productName",
            "categoryName",
            "specs",
            "physicalCustomization",
            "createdAt",
            "status",
            "statusLabel",
            "hasResponse",
            "latestResponse",
            "photos",
            "files",
            "created_at",
        ]

    def get_photos(self, obj):
        return _request_photo_payload(obj, self.context.get("request"))

    def get_files(self, obj):
        request = self.context.get("request")
        rows = []
        for item in obj.files.all():
            url = item.file.url if item.file else ""
            if request and url:
                url = request.build_absolute_uri(url)
            rows.append({
                "id": item.id,
                "name": item.original_name or "",
                "url": url,
            })
        return rows

    def get_categoryName(self, obj):
        snapshot = obj.specs_snapshot or {}
        if snapshot.get("category_name"):
            return snapshot["category_name"]
        if obj.category_id and obj.category:
            return obj.category.name_fr or obj.category.name_en
        return ""

    def get_specs(self, obj):
        return list((obj.specs_snapshot or {}).get("fields") or [])

    def get_physicalCustomization(self, obj):
        snapshot = obj.specs_snapshot or {}
        return snapshot.get("physical_customization_label") or ""

    def get_createdAt(self, obj):
        return obj.created_at.strftime("%d/%m/%Y")

    def get_statusLabel(self, obj):
        return QUOTE_STATUS_LABELS.get(obj.status, obj.status)

    def get_productName(self, obj):
        if obj.product_id and obj.product:
            return obj.product.name
        return ""

    def get_hasResponse(self, obj):
        if hasattr(obj, "_prefetched_responses"):
            return bool(obj._prefetched_responses)
        return obj.responses.exists()

    def get_latestResponse(self, obj):
        response = None
        if hasattr(obj, "_prefetched_responses") and obj._prefetched_responses:
            response = obj._prefetched_responses[0]
        else:
            response = obj.responses.order_by("-created_at").first()
        if not response:
            return None
        return {
            "id": response.id,
            "templateName": response.proposal_template_name or "",
            "createdAt": response.created_at.strftime("%d/%m/%Y"),
            "createdAtIso": response.created_at.isoformat(),
            "status": response.status,
            "statusLabel": response.get_status_display(),
            "proposedPrice": response.proposed_price,
            "moq": response.moq,
            "leadTimeDays": response.lead_time_days,
            "customizationFee": response.customization_fee,
            "commercialTerms": response.commercial_terms,
        }


class ProductOrderDetailSerializer(SupplierProductSerializer):
    supplierId = serializers.IntegerField(source="supplier_id", read_only=True)
    supplierBrandColor = serializers.CharField(
        source="supplier.brand_color",
        allow_null=True,
        read_only=True,
    )
    supplierBrandButtonColor = serializers.CharField(
        source="supplier.brand_button_color",
        allow_null=True,
        read_only=True,
    )
    supplierCoverColor = serializers.SerializerMethodField()
    supplierLogo = serializers.SerializerMethodField()
    supplierLogoEmoji = serializers.SerializerMethodField()
    minOrderQuantity = serializers.IntegerField(source="min_order_quantity", read_only=True)
    unitPrice = serializers.DecimalField(
        source="price", max_digits=10, decimal_places=2, allow_null=True, read_only=True
    )
    description = serializers.CharField(read_only=True)
    descriptionHtml = serializers.CharField(source="description_html", read_only=True)
    targetMarkets = serializers.SerializerMethodField()
    targetMarketLabels = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    attributeValues = serializers.SerializerMethodField()
    technicalSheet = serializers.SerializerMethodField()
    technicalSheetPdfUrl = serializers.SerializerMethodField()
    similarProducts = serializers.SerializerMethodField()
    subcategoryId = serializers.IntegerField(source="subcategory_id", allow_null=True, read_only=True)
    categoryId = serializers.IntegerField(source="category_id", allow_null=True, read_only=True)

    class Meta(SupplierProductSerializer.Meta):
        fields = SupplierProductSerializer.Meta.fields + [
            "categoryId",
            "supplierId",
            "supplierBrandColor",
            "supplierBrandButtonColor",
            "supplierCoverColor",
            "supplierLogo",
            "supplierLogoEmoji",
            "minOrderQuantity",
            "unitPrice",
            "description",
            "descriptionHtml",
            "targetMarkets",
            "targetMarketLabels",
            "images",
            "attributeValues",
            "technicalSheet",
            "technicalSheetPdfUrl",
            "similarProducts",
        ]

    def get_targetMarkets(self, obj):
        return normalize_target_markets(obj.target_markets)

    def get_supplierCoverColor(self, obj):
        if obj.supplier.brand_color:
            return obj.supplier.brand_color
        return "#e8642c"

    def get_supplierLogo(self, obj):
        if not obj.supplier.logo:
            return None
        request = self.context.get("request")
        url = obj.supplier.logo.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def get_supplierLogoEmoji(self, obj):
        return "🏭"

    def get_targetMarketLabels(self, obj):
        return target_market_labels(obj.target_markets)

    def get_attributeValues(self, obj):
        return _read_product_attribute_values(obj)

    def get_technicalSheet(self, obj):
        return _product_technical_sheet_rows(obj)

    def get_technicalSheetPdfUrl(self, obj):
        return f"/api/products/{obj.pk}/technical-sheet/"

    def get_similarProducts(self, obj):
        similar = find_similar_products(obj, limit=5)
        return SupplierProductSerializer(
            similar,
            many=True,
            context=self.context,
        ).data

    def get_images(self, obj):
        request = self.context.get("request")
        images = [
            _serialize_product_image(img, request)
            for img in obj.images.all().order_by("-is_primary", "id")
        ]
        if images:
            return images
        fallback = _product_image_url(obj, request)
        if fallback:
            return [{"id": 0, "url": fallback, "isPrimary": True}]
        return []


TECH_PREFERENCE_MAX_LENGTH = 1000
BUYER_NOTE_MAX_LENGTH = 1000


class OrderCreateSerializer(serializers.Serializer):
    productId = serializers.IntegerField()
    variantId = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    # Optional technical preference: a wish, never a guarantee of availability.
    specifications = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=TECH_PREFERENCE_MAX_LENGTH,
        trim_whitespace=True,
    )
    guestName = serializers.CharField(required=False, allow_blank=True, max_length=255)
    phoneCountryCode = serializers.CharField(required=False, allow_blank=True, max_length=8)
    guestPhone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    wilaya = serializers.CharField(required=False, allow_blank=True, max_length=100)
    tradeScope = serializers.CharField(required=False, allow_blank=True, default="local")
    destinationCountry = serializers.CharField(required=False, allow_blank=True, max_length=8)
    destinationCity = serializers.CharField(required=False, allow_blank=True, max_length=160)
    incoterm = serializers.CharField(required=False, allow_blank=True, max_length=8)
    buyerNote = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=BUYER_NOTE_MAX_LENGTH
    )

    def _validate_trade(self, attrs):
        from rest_framework.exceptions import ValidationError as DRFValidationError

        from user.export_terms import (
            TRADE_EXPORT,
            clean_export_country,
            clean_incoterm,
            clean_trade_scope,
        )

        try:
            scope = clean_trade_scope(attrs.get("tradeScope"))
            attrs["trade_scope"] = scope
            if scope == TRADE_EXPORT:
                attrs["destination_country"] = clean_export_country(
                    attrs.get("destinationCountry"), required=True
                )
                attrs["destination_city"] = (attrs.get("destinationCity") or "").strip()
                attrs["incoterm"] = clean_incoterm(attrs.get("incoterm"))
                attrs["buyer_note"] = (attrs.get("buyerNote") or "").strip()
        except DRFValidationError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
            raise serializers.ValidationError(detail) from exc
        return scope

    def validate(self, attrs):
        from user.export_terms import TRADE_EXPORT

        scope = self._validate_trade(attrs)
        try:
            product = Product.objects.get(
                pk=attrs["productId"],
                is_active=True,
                supplier__is_active=True,
                supplier__is_ghost=False,
            )
        except Product.DoesNotExist:
            raise serializers.ValidationError({"productId": "Produit introuvable."})
        from user.catalog_taxonomy import order_is_allowed

        variant = None
        variant_id = attrs.get("variantId")
        if variant_id:
            variant = ProductVariant.objects.filter(pk=variant_id, product=product).first()
            if variant is None:
                raise serializers.ValidationError(
                    {"variantId": "Variante introuvable pour ce produit."}
                )
        if not order_is_allowed(product):
            raise serializers.ValidationError(
                {
                    "productId": (
                        "Ce produit est fabriqué sur demande : "
                        "envoyez une demande de devis au fournisseur."
                    )
                }
            )
        minimum = product.min_order_quantity
        if variant is not None and variant.moq:
            minimum = variant.moq
        if attrs["quantity"] < minimum:
            raise serializers.ValidationError(
                {
                    "quantity": (
                        f"La quantité minimum est de {minimum} unités."
                    )
                }
            )
        attrs["variant"] = variant

        request = self.context.get("request")
        is_authenticated = bool(
            request and getattr(request, "user", None) and request.user.is_authenticated
        )
        if not is_authenticated:
            guest_name = (attrs.get("guestName") or "").strip()
            guest_phone = (attrs.get("guestPhone") or "").strip()
            country_code = (attrs.get("phoneCountryCode") or "").strip() or "+213"
            errors = {}
            if not guest_name:
                errors["guestName"] = "Le nom est obligatoire."
            if not guest_phone:
                errors["guestPhone"] = "Le numéro de téléphone est obligatoire."
            elif not guest_phone.replace(" ", "").isdigit():
                errors["guestPhone"] = "Numéro de téléphone invalide."
            if not country_code.startswith("+"):
                errors["phoneCountryCode"] = "Indicatif invalide."
            if errors:
                raise serializers.ValidationError(errors)
            attrs["guest_name"] = guest_name
            attrs["guest_phone"] = guest_phone
            attrs["guest_phone_country_code"] = country_code
        elif scope != TRADE_EXPORT:
            wilaya = (attrs.get("wilaya") or "").strip()
            if not wilaya:
                raise serializers.ValidationError(
                    {"wilaya": "La wilaya est obligatoire."}
                )
            valid_wilayas = {choice[0] for choice in Supplier.WILAYA_CHOICES}
            if wilaya not in valid_wilayas:
                raise serializers.ValidationError({"wilaya": "Wilaya invalide."})
            attrs["wilaya"] = wilaya

        attrs["product"] = product
        return attrs


def contact_display_name(user):
    name = (user.get_full_name() or "").strip()
    if name:
        return name
    if user.first_name:
        return user.first_name
    if user.company_name:
        return user.company_name
    email = (getattr(user, "email", None) or "").strip()
    if email:
        return email.split("@")[0]
    return "Acheteur"


def user_avatar_url(request, user):
    """Buyer profile picture, or supplier logo for supplier accounts."""
    supplier_id = getattr(user, "supplier_id", None)
    if supplier_id:
        supplier = getattr(user, "supplier", None)
        if supplier is None:
            supplier = Supplier.objects.filter(pk=supplier_id).only("logo").first()
        if supplier and supplier.logo:
            url = supplier.logo.url
            if request is not None:
                return request.build_absolute_uri(url)
            return url
    if user.profile_picture:
        url = user.profile_picture.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url
    return None


class SupplierContactSerializer(serializers.Serializer):
    buyerId = serializers.IntegerField()
    name = serializers.CharField()
    company = serializers.CharField(allow_blank=True)
    profilePicture = serializers.CharField(allow_null=True, allow_blank=True)
    lastMessage = serializers.CharField(allow_blank=True, allow_null=True)
    lastMessageAt = serializers.DateTimeField(allow_null=True)
    unreadCount = serializers.IntegerField()


class SupplierMessageSerializer(serializers.ModelSerializer):
    senderId = serializers.IntegerField(source="sender_id", read_only=True)
    senderAvatar = serializers.SerializerMethodField()
    isMine = serializers.SerializerMethodField()
    sentAt = serializers.DateTimeField(source="created_at", read_only=True)
    sentAtLabel = serializers.SerializerMethodField()
    viewedAtLabel = serializers.SerializerMethodField()
    messageType = serializers.CharField(source="message_type", read_only=True)
    proposalDocumentUrl = serializers.SerializerMethodField()
    proposalKind = serializers.SerializerMethodField()
    proposalResponseId = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "content",
            "senderId",
            "senderAvatar",
            "isMine",
            "sentAt",
            "sentAtLabel",
            "viewedAtLabel",
            "messageType",
            "proposalDocumentUrl",
            "proposalKind",
            "proposalResponseId",
        ]

    def get_proposalDocumentUrl(self, obj):
        if obj.message_type != Message.MessageType.PROPOSAL:
            return None
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        path = (meta.get("documentPath") or "").strip()
        if not path:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(path)
        return path

    def get_proposalKind(self, obj):
        if obj.message_type != Message.MessageType.PROPOSAL:
            return None
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        return meta.get("proposalKind")

    def get_proposalResponseId(self, obj):
        if obj.message_type != Message.MessageType.PROPOSAL:
            return None
        meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        raw = meta.get("responseId")
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def get_senderAvatar(self, obj):
        request = self.context.get("request")
        return user_avatar_url(request, obj.sender)

    def get_isMine(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return obj.sender_id == request.user.id
        return False

    def get_sentAtLabel(self, obj):
        return obj.created_at.strftime("Envoyé le %d/%m/%Y à %H:%M")

    def get_viewedAtLabel(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if obj.sender_id != request.user.id:
            return None

        read_at = None
        if request.user.supplier_id:
            if obj.read_by_buyer:
                read_at = obj.read_by_buyer_at
        elif obj.read_by_supplier:
            read_at = obj.read_by_supplier_at

        if not read_at:
            return None
        local = timezone.localtime(read_at)
        return local.strftime("Vue %H:%M le %d/%m/%y")


class SupplierMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=5000, trim_whitespace=True)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le message ne peut pas être vide.")
        return value.strip()



class BuyerOrderSerializer(OrderExecutionFieldsMixin, serializers.ModelSerializer):
    productName = serializers.CharField(source="product.name", read_only=True)
    productId = serializers.IntegerField(source="product_id", read_only=True)
    variantId = serializers.IntegerField(source="variant_id", read_only=True, allow_null=True)
    variantLabel = serializers.SerializerMethodField()
    launchDate = serializers.SerializerMethodField()
    launchTime = serializers.SerializerMethodField()
    leadTimeDays = serializers.IntegerField(source="product.lead_time_days", read_only=True)
    statusLabel = serializers.SerializerMethodField()
    canCancel = serializers.SerializerMethodField()
    canRate = serializers.SerializerMethodField()
    reviewRating = serializers.SerializerMethodField()
    reviewComment = serializers.SerializerMethodField()
    supplierId = serializers.IntegerField(source="supplier_id", read_only=True)
    supplierName = serializers.CharField(source="supplier.company_name", read_only=True)
    supplierLogo = serializers.SerializerMethodField()
    supplierPhone = serializers.SerializerMethodField()
    supplierLocation = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "productName",
            "productId",
            "variantId",
            "variantLabel",
            "launchDate",
            "launchTime",
            "quantity",
            "leadTimeDays",
            "status",
            "statusLabel",
            "canCancel",
            "canRate",
            "reviewRating",
            "reviewComment",
            "supplierId",
            "supplierName",
            "supplierLogo",
            "supplierPhone",
            "supplierLocation",
            *OrderExecutionFieldsMixin.EXECUTION_FIELDS,
        ]

    def get_variantLabel(self, obj):
        from user.catalog_taxonomy import format_variant_axes

        variant = getattr(obj, "variant", None)
        if variant is None:
            return ""
        return format_variant_axes(variant.axes)

    def get_launchDate(self, obj):
        return obj.created_at.strftime("%d/%m/%Y")

    def get_launchTime(self, obj):
        return obj.created_at.strftime("%H:%M")

    def get_statusLabel(self, obj):
        return ORDER_STATUS_LABELS.get(obj.status, obj.status)

    def get_canCancel(self, obj):
        return obj.status in BUYER_CANCELLABLE

    def get_canRate(self, obj):
        return obj.status in RATEABLE_STATUSES

    def _supplier_review(self, obj):
        try:
            return obj.supplier_review
        except OrderSupplierReview.DoesNotExist:
            return None

    def get_reviewRating(self, obj):
        review = self._supplier_review(obj)
        return review.rating if review else None

    def get_reviewComment(self, obj):
        review = self._supplier_review(obj)
        return review.comment if review else ""

    def get_supplierLogo(self, obj):
        request = self.context.get("request")
        if obj.supplier.logo and request:
            return request.build_absolute_uri(obj.supplier.logo.url)
        if obj.supplier.logo:
            return obj.supplier.logo.url
        return None

    def get_supplierPhone(self, obj):
        return obj.supplier.whatsapp_number or "—"

    def get_supplierLocation(self, obj):
        if obj.supplier.wilaya:
            return obj.supplier.get_wilaya_display()
        return obj.supplier.address or "—"


class BuyerContactSerializer(serializers.Serializer):
    supplierId = serializers.IntegerField()
    name = serializers.CharField()
    company = serializers.CharField(allow_blank=True)
    logo = serializers.CharField(allow_null=True, allow_blank=True)
    lastMessage = serializers.CharField(allow_blank=True)
    lastMessageAt = serializers.DateTimeField(allow_null=True)
    unreadCount = serializers.IntegerField()


class BuyerSearchHistorySerializer(serializers.ModelSerializer):
    date = serializers.SerializerMethodField()
    time = serializers.SerializerMethodField()

    class Meta:
        model = BuyerSearchHistory
        fields = ["id", "query", "rating", "date", "time", "created_at"]

    def get_date(self, obj):
        return obj.created_at.strftime("%d/%m/%Y")

    def get_time(self, obj):
        return obj.created_at.strftime("%H:%M")


class BuyerFavoriteSupplierSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="supplier.id")
    companyName = serializers.CharField(source="supplier.company_name")
    location = serializers.SerializerMethodField()
    logo = serializers.SerializerMethodField()
    brandColor = serializers.CharField(source="supplier.brand_color", allow_null=True)
    categories = serializers.SerializerMethodField()
    productCount = serializers.SerializerMethodField()

    class Meta:
        model = BuyerFavoriteSupplier
        fields = [
            "id",
            "companyName",
            "location",
            "logo",
            "brandColor",
            "categories",
            "productCount",
            "created_at",
        ]

    def get_location(self, obj):
        if obj.supplier.wilaya:
            return obj.supplier.get_wilaya_display()
        return obj.supplier.address or ""

    def get_logo(self, obj):
        request = self.context.get("request")
        if obj.supplier.logo and request:
            return request.build_absolute_uri(obj.supplier.logo.url)
        if obj.supplier.logo:
            return obj.supplier.logo.url
        return None

    def get_categories(self, obj):
        return list(
            obj.supplier.interested_categories.values_list("name_fr", flat=True)[:3]
        ) or list(
            obj.supplier.products.filter(is_active=True)
            .values_list("category__name_fr", flat=True)
            .distinct()[:3]
        )

    def get_productCount(self, obj):
        return obj.supplier.products.filter(is_active=True).count()


class BuyerProfileDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField(read_only=True)
    company = serializers.CharField(max_length=255, allow_blank=True)
    phone = serializers.CharField(max_length=20, allow_blank=True)
    wilaya = serializers.CharField(max_length=100, allow_blank=True)
    address = serializers.CharField(allow_blank=True)
    natureOfActivity = serializers.CharField(max_length=100, allow_blank=True)
    annualPurchaseVolume = serializers.CharField(max_length=100, allow_blank=True)
    profilePicture = serializers.CharField(read_only=True, allow_null=True)
    wilayaOptions = serializers.ListField(
        child=serializers.DictField(),
        read_only=True,
        required=False,
    )
    interestedSubcategoryIds = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        read_only=True,
        required=False,
    )


class BuyerUpgradeSupplierSerializer(serializers.Serializer):
    companyName = serializers.CharField(max_length=255)
    whatsappNumber = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    wilaya = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    address = serializers.CharField(required=False, allow_blank=True, default="")
    website = serializers.URLField(required=False, allow_blank=True, default="")
    presentation = serializers.CharField(required=False, allow_blank=True, default="")
    type = serializers.ChoiceField(
        choices=["manufacturer", "distributor"],
        required=False,
        allow_blank=True,
        default="",
    )
    categoryIds = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
    )
    subcategoryIds = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
    )

    def validate_companyName(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Le nom de l'entreprise est obligatoire.")
        return value

    def validate_categoryIds(self, value):
        if not value:
            return []
        found = Category.objects.filter(id__in=value, parent__isnull=True).count()
        if found != len(set(value)):
            raise serializers.ValidationError("Une ou plusieurs catégories sont invalides.")
        return list(dict.fromkeys(value))

    def validate_subcategoryIds(self, value):
        if not value:
            return []
        found = Category.objects.filter(id__in=value, parent__isnull=False).count()
        if found != len(set(value)):
            raise serializers.ValidationError(
                "Une ou plusieurs sous-catégories sont invalides."
            )
        return list(dict.fromkeys(value))


class SupplierProfileEditSerializer(serializers.ModelSerializer):
    companyName = serializers.CharField(source="company_name")
    whatsappNumber = serializers.CharField(
        source="whatsapp_number", allow_blank=True, required=False
    )
    instagramUrl = serializers.URLField(
        source="instagram_url", allow_blank=True, allow_null=True, required=False
    )
    facebookUrl = serializers.URLField(
        source="facebook_url", allow_blank=True, allow_null=True, required=False
    )
    linkedinUrl = serializers.URLField(
        source="linkedin_url", allow_blank=True, allow_null=True, required=False
    )
    presentation = serializers.CharField(allow_blank=True, required=False)
    presentationHtml = serializers.CharField(
        source="presentation_html",
        allow_blank=True,
        required=False,
        trim_whitespace=False,
        max_length=RICH_HTML_MAX_LENGTH,
    )
    brandColor = serializers.CharField(
        source="brand_color", allow_blank=True, required=False
    )
    brandButtonColor = serializers.CharField(
        source="brand_button_color", allow_blank=True, required=False
    )
    brandNeutralColor = serializers.CharField(
        source="brand_neutral_color", required=False
    )
    logo = serializers.SerializerMethodField()
    typeLabel = serializers.SerializerMethodField()
    categoryIds = serializers.SerializerMethodField()
    subcategoryIds = serializers.SerializerMethodField()
    wilayaLabel = serializers.SerializerMethodField()
    subscriptionTier = serializers.SerializerMethodField()

    class Meta:
        model = Supplier
        fields = [
            "id",
            "companyName",
            "email",
            "whatsappNumber",
            "wilaya",
            "wilayaLabel",
            "address",
            "website",
            "instagramUrl",
            "facebookUrl",
            "linkedinUrl",
            "presentation",
            "presentationHtml",
            "type",
            "typeLabel",
            "brandColor",
            "brandButtonColor",
            "brandNeutralColor",
            "logo",
            "categoryIds",
            "subcategoryIds",
            "subscriptionTier",
        ]

    def validate_brandNeutralColor(self, value):
        normalized = (value or "#ffffff").strip().lower()
        if normalized in ("#000000", "#000", "black", "noir"):
            return "#000000"
        if normalized in ("#ffffff", "#fff", "white", "blanc", ""):
            return "#ffffff"
        raise serializers.ValidationError(
            "La couleur neutre doit être blanc (#ffffff) ou noir (#000000)."
        )

    def get_logo(self, obj):
        request = self.context.get("request")
        if obj.logo and request:
            return request.build_absolute_uri(obj.logo.url)
        if obj.logo:
            return obj.logo.url
        return None

    def get_typeLabel(self, obj):
        return obj.get_type_display() if obj.type else ""

    def get_wilayaLabel(self, obj):
        return obj.get_wilaya_display() if obj.wilaya else ""

    def get_categoryIds(self, obj):
        return list(obj.interested_categories.values_list("id", flat=True))

    def get_subcategoryIds(self, obj):
        return list(obj.interested_subcategories.values_list("id", flat=True))

    def get_subscriptionTier(self, obj):
        return get_supplier_subscription_tier(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for key in (
            "whatsappNumber",
            "wilaya",
            "address",
            "website",
            "instagramUrl",
            "facebookUrl",
            "linkedinUrl",
            "presentation",
            "type",
            "brandColor",
            "brandButtonColor",
            "brandNeutralColor",
        ):
            if data.get(key) is None:
                data[key] = ""
        if not data.get("brandNeutralColor"):
            data["brandNeutralColor"] = "#ffffff"
        return data

    def update(self, instance, validated_data):
        category_ids = self.context.get("category_ids")
        subcategory_ids = self.context.get("subcategory_ids")
        logo = self.context.get("logo_file")

        _drop_stale_rich_html(instance, validated_data, "presentation_html", "presentation")
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if category_ids is not None:
            instance.interested_categories.set(category_ids)
        if subcategory_ids is not None:
            instance.interested_subcategories.set(subcategory_ids)
        if logo:
            instance.logo = logo
            instance.save(update_fields=["logo", "updated_at"])

        return instance


class SupplierSettingsSerializer(serializers.ModelSerializer):
    productCardStyle = serializers.JSONField(source="product_card_style")
    faqItems = serializers.JSONField(source="faq_items")
    listingFormFields = serializers.JSONField(source="listing_form_fields")
    listingCardFields = serializers.JSONField(source="listing_card_fields")
    listingRules = serializers.JSONField(source="listing_rules")
    proposalTemplate = serializers.CharField(source="proposal_template", required=False, allow_blank=True)
    proposalBlocks = serializers.JSONField(source="proposal_blocks")
    proposalTemplates = serializers.JSONField(source="proposal_templates", required=False)
    pageDesign = serializers.JSONField(source="page_design")
    subscriptionTier = serializers.SerializerMethodField()

    class Meta:
        model = SupplierSettings
        fields = [
            "productCardStyle",
            "faqItems",
            "listingFormFields",
            "listingCardFields",
            "listingRules",
            "proposalTemplate",
            "proposalBlocks",
            "proposalTemplates",
            "pageDesign",
            "subscriptionTier",
        ]

    def get_subscriptionTier(self, obj):
        return get_supplier_subscription_tier(obj.supplier)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        templates = _normalize_proposal_templates(
            data.get("proposalTemplates"),
            legacy_blocks=data.get("proposalBlocks"),
        )
        data["proposalTemplates"] = templates
        default_tpl = _default_proposal_template(templates)
        if default_tpl:
            data["proposalBlocks"] = default_tpl.get("blocks") or []
        return data

    def validate_faqItems(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("faqItems must be a list.")
        cleaned = []
        for item in value:
            if not isinstance(item, dict):
                continue
            question = (item.get("question") or "").strip()
            answer = (item.get("answer") or "").strip()
            if question and answer:
                cleaned.append({"question": question, "answer": answer})
        return cleaned

    def validate_productCardStyle(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("productCardStyle must be an object.")
        layout = str(value.get("layout") or "vertical").lower()
        if layout not in {"vertical", "square", "horizontal"}:
            value = {**value, "layout": "vertical"}
        else:
            value = {**value, "layout": layout}
        horizontal_images = str(value.get("horizontalImages") or "1").strip()
        value["horizontalImages"] = "2" if horizontal_images == "2" else "1"
        return value

    def validate_listingRules(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("listingRules must be a list.")
        return value

    def validate_proposalBlocks(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("proposalBlocks must be a list.")
        return value

    def validate_proposalTemplates(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("proposalTemplates must be a list.")
        return _normalize_proposal_templates(value)

    def validate_pageDesign(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("pageDesign must be an object.")
        tier = str(value.get("storeHeaderTier") or "standard").lower()
        if tier not in {"standard", "extra", "central", "mega"}:
            tier = "standard"
        use_in_search = str(value.get("useStoreHeaderInSearch", "")).lower() in (
            "true",
            "1",
            "yes",
        )
        if tier not in {"extra", "central", "mega"}:
            use_in_search = False
        show_rating = str(value.get("showRating", "true")).lower() not in (
            "false",
            "0",
            "no",
        )
        value = {
            **value,
            "storeHeaderTier": tier,
            "showRating": "true" if show_rating else "false",
            "useStoreHeaderInSearch": "true" if use_in_search else "false",
            "samplesEnabled": (
                "true" if normalize_samples_enabled(value.get("samplesEnabled")) else "false"
            ),
            "sampleQuantity": str(
                normalize_sample_quantity(value.get("sampleQuantity", DEFAULT_SAMPLE_QUANTITY))
            ),
        }
        return value

    def update(self, instance, validated_data):
        faq_items = validated_data.get("faq_items")
        proposal_blocks = validated_data.get("proposal_blocks")
        proposal_templates = validated_data.get("proposal_templates")

        if proposal_templates is not None:
            normalized = _normalize_proposal_templates(proposal_templates)
            previous = _normalize_proposal_templates(
                instance.proposal_templates, instance.proposal_blocks
            )
            validated_data["proposal_templates"] = _enforce_proposal_template_billing(
                previous, normalized
            )
            default_tpl = _default_proposal_template(validated_data["proposal_templates"])
            if default_tpl:
                validated_data["proposal_blocks"] = default_tpl.get("blocks") or []
                validated_data["proposal_template"] = render_proposal_blocks_to_text(
                    validated_data["proposal_blocks"]
                )
                if faq_items is None:
                    faq_items = _faq_items_from_proposal_blocks(validated_data["proposal_blocks"])
                    if faq_items:
                        validated_data["faq_items"] = faq_items

        if proposal_blocks and faq_items is None and proposal_templates is None:
            faq_from_blocks = _faq_items_from_proposal_blocks(proposal_blocks)
            if faq_from_blocks:
                validated_data["faq_items"] = faq_from_blocks
        return super().update(instance, validated_data)

    def validate_listingFormFields(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("listingFormFields must be a list.")
        return list(dict.fromkeys(str(v) for v in value if str(v).strip()))

    def validate_listingCardFields(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("listingCardFields must be a list.")
        return list(dict.fromkeys(str(v) for v in value if str(v).strip()))


class SupplierProposalRespondSerializer(serializers.Serializer):
    templateId = serializers.CharField(required=False, allow_blank=True)
    templateName = serializers.CharField(required=False, allow_blank=True)
    blocks = serializers.ListField(required=False, allow_empty=True)
    message = serializers.CharField(required=False, allow_blank=True)
    proposedPrice = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    leadTimeDays = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    moq = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    customizationFee = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=Decimal("0")
    )
    commercialTerms = serializers.CharField(required=False, allow_blank=True, max_length=4000)

    def validate_blocks(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("blocks must be a list.")
        return value


SupplierRfqRespondSerializer = SupplierProposalRespondSerializer
SupplierOrderRespondSerializer = SupplierProposalRespondSerializer


class OrderResponseSerializer(serializers.ModelSerializer):
    createdAt = serializers.SerializerMethodField()
    statusLabel = serializers.SerializerMethodField()
    templateId = serializers.CharField(source="proposal_template_id", read_only=True)
    templateName = serializers.CharField(source="proposal_template_name", read_only=True)
    proposalBlocks = serializers.JSONField(source="proposal_blocks", read_only=True)
    proposedPrice = serializers.DecimalField(
        source="proposed_price", max_digits=12, decimal_places=2, allow_null=True, read_only=True
    )
    leadTimeDays = serializers.IntegerField(source="lead_time_days", allow_null=True, read_only=True)

    class Meta:
        model = OrderResponse
        fields = [
            "id",
            "message",
            "templateId",
            "templateName",
            "proposalBlocks",
            "proposedPrice",
            "leadTimeDays",
            "status",
            "statusLabel",
            "createdAt",
            "created_at",
        ]

    def get_createdAt(self, obj):
        return obj.created_at.strftime("%d/%m/%Y")

    def get_statusLabel(self, obj):
        return obj.get_status_display()


class QuoteResponseSerializer(serializers.ModelSerializer):
    createdAt = serializers.SerializerMethodField()
    statusLabel = serializers.SerializerMethodField()
    templateId = serializers.CharField(source="proposal_template_id", read_only=True)
    templateName = serializers.CharField(source="proposal_template_name", read_only=True)
    proposalBlocks = serializers.JSONField(source="proposal_blocks", read_only=True)
    proposedPrice = serializers.DecimalField(
        source="proposed_price", max_digits=12, decimal_places=2, allow_null=True, read_only=True
    )
    leadTimeDays = serializers.IntegerField(source="lead_time_days", allow_null=True, read_only=True)
    moq = serializers.IntegerField(allow_null=True, read_only=True)
    customizationFee = serializers.DecimalField(
        source="customization_fee",
        max_digits=12,
        decimal_places=2,
        allow_null=True,
        read_only=True,
    )
    commercialTerms = serializers.CharField(source="commercial_terms", read_only=True)

    class Meta:
        model = QuoteResponse
        fields = [
            "id",
            "message",
            "templateId",
            "templateName",
            "proposalBlocks",
            "proposedPrice",
            "leadTimeDays",
            "moq",
            "customizationFee",
            "commercialTerms",
            "status",
            "statusLabel",
            "createdAt",
        ]

    def get_createdAt(self, obj):
        return obj.created_at.strftime("%d/%m/%Y %H:%M")

    def get_statusLabel(self, obj):
        return obj.get_status_display()


class EventSerializer(serializers.ModelSerializer):
    eventType = serializers.CharField(source="event_type", read_only=True)
    eventLabel = serializers.SerializerMethodField()
    actorRole = serializers.CharField(source="actor_role", read_only=True)
    actorUserId = serializers.IntegerField(source="actor_user_id", read_only=True)
    actorEmail = serializers.SerializerMethodField()
    actorName = serializers.SerializerMethodField()
    geoCountry = serializers.SerializerMethodField()
    geoWilaya = serializers.SerializerMethodField()
    side = serializers.CharField(read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "eventType",
            "eventLabel",
            "actorRole",
            "actorUserId",
            "actorEmail",
            "actorName",
            "geoCountry",
            "geoWilaya",
            "side",
            "context",
            "createdAt",
        ]

    def get_eventLabel(self, obj):
        return obj.get_event_type_display()

    def get_actorEmail(self, obj):
        if obj.actor_user:
            return obj.actor_user.email
        return None

    def get_actorName(self, obj):
        if obj.actor_user:
            return contact_display_name(obj.actor_user)
        return None

    def get_geoCountry(self, obj):
        from user.geo_analytics import event_country_label

        return event_country_label(obj.context, actor_user=obj.actor_user)

    def get_geoWilaya(self, obj):
        from user.geo_analytics import event_wilaya_label

        label = event_wilaya_label(obj)
        from user.geo_analytics import UNKNOWN_WILAYA_LABEL

        return "" if label == UNKNOWN_WILAYA_LABEL else label


class SupportMessageSerializer(serializers.ModelSerializer):
    senderId = serializers.IntegerField(source="sender_id", read_only=True)
    senderAvatar = serializers.SerializerMethodField()
    isMine = serializers.SerializerMethodField()
    isStaffReply = serializers.BooleanField(source="is_staff_reply", read_only=True)
    sentAt = serializers.DateTimeField(source="created_at", read_only=True)
    sentAtLabel = serializers.SerializerMethodField()

    class Meta:
        model = SupportMessage
        fields = [
            "id",
            "content",
            "senderId",
            "senderAvatar",
            "isMine",
            "isStaffReply",
            "sentAt",
            "sentAtLabel",
        ]

    def get_senderAvatar(self, obj):
        request = self.context.get("request")
        return user_avatar_url(request, obj.sender)

    def get_isMine(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return obj.sender_id == request.user.id
        return False

    def get_sentAtLabel(self, obj):
        return obj.created_at.strftime("Envoyé le %d/%m/%Y à %H:%M")


class SupportConversationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    userId = serializers.IntegerField()
    userName = serializers.CharField()
    userEmail = serializers.EmailField()
    userCompany = serializers.CharField()
    userAvatar = serializers.CharField(allow_null=True)
    isSupplier = serializers.BooleanField()
    subject = serializers.CharField()
    status = serializers.CharField()
    lastMessage = serializers.CharField()
    lastMessageAt = serializers.DateTimeField()
    unreadCount = serializers.IntegerField()
    createdAt = serializers.DateTimeField()


class SupplierFavoriteActivitySerializer(serializers.Serializer):
    id = serializers.CharField()
    kind = serializers.CharField()
    buyerId = serializers.IntegerField()
    buyerName = serializers.CharField()
    buyerCompany = serializers.CharField()
    profilePicture = serializers.CharField(allow_null=True)
    productId = serializers.IntegerField(allow_null=True)
    productName = serializers.CharField(allow_null=True)
    createdAt = serializers.DateTimeField()
