"""Supplier-proposed technical attributes, and the admin step that generalises them.

A proposal lives on the supplier's products only. Generalising it attaches the
attribute to the category, so every supplier of that type can fill it.
"""

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from user.models import (
    CategoryAttribute,
    ProductAttributeValue,
    TechnicalAttribute,
    TechnicalAttributeProposal,
    Unit,
)

MAX_PROPOSED_ATTRIBUTES = 12
PROPOSED_TYPES = ("text", "number", "boolean")
PRIVATE_STATUSES = (
    TechnicalAttributeProposal.PENDING,
    TechnicalAttributeProposal.REJECTED,
)


def _clean_name(value) -> str:
    return " ".join(str(value or "").split())


def _value_text(row) -> str:
    if row.num_value is not None:
        return str(row.num_value)
    if row.txt_value:
        return row.txt_value
    return "true" if row.bool_value else "false"


def unit_for_symbol(symbol: str):
    symbol = _clean_name(symbol)[:20]
    if not symbol:
        return None
    unit = Unit.objects.filter(Q(symbol__iexact=symbol) | Q(name__iexact=symbol)).first()
    if unit:
        return unit
    return Unit.objects.create(name=symbol, symbol=symbol)


def normalize_proposed_attributes(items) -> list[dict]:
    """Client payload → unique name/type/value rows. Blank values are dropped."""
    if not isinstance(items, list):
        return []
    cleaned = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_name(item.get("name"))
        if not name or len(name) > 150:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        attribute_type = item.get("attributeType") or "text"
        if attribute_type not in PROPOSED_TYPES:
            attribute_type = "text"
        value = str(item.get("value") or "").strip()
        if attribute_type == "boolean":
            value = "true" if value.lower() in ("true", "1", "yes", "oui") else "false"
        elif not value:
            continue
        raw_id = item.get("id")
        try:
            attribute_id = int(raw_id) if raw_id else None
        except (TypeError, ValueError):
            attribute_id = None
        cleaned.append(
            {
                "id": attribute_id,
                "name": name,
                "attributeType": attribute_type,
                "unitSymbol": _clean_name(item.get("unitSymbol"))[:20],
                "value": value,
            }
        )
        if len(cleaned) >= MAX_PROPOSED_ATTRIBUTES:
            break
    return cleaned


def _target_category(category, subcategory):
    return subcategory or category


def _catalog_match(target, name: str):
    if target is None or not name:
        return None
    categories = [target]
    if getattr(target, "parent_id", None):
        categories.append(target.parent)
    return (
        TechnicalAttribute.objects.filter(category_attributes__category__in=categories)
        .filter(Q(name_fr__iexact=name) | Q(name_en__iexact=name))
        .exclude(proposal__status__in=PRIVATE_STATUSES)
        .distinct()
        .first()
    )


def _private_proposal(supplier_id, target, *, attribute_id=None, name=""):
    qs = TechnicalAttributeProposal.objects.select_related("attribute").filter(
        supplier_id=supplier_id,
        category=target,
        status__in=PRIVATE_STATUSES,
    )
    if attribute_id:
        found = qs.filter(attribute_id=attribute_id).first()
        if found:
            return found
    if name:
        return qs.filter(attribute__name_fr__iexact=name).first()
    return None


def upsert_supplier_proposed_attributes(supplier_id, category, subcategory, items) -> list[dict]:
    """Create or reuse this supplier's private attributes and return value rows."""
    target = _target_category(category, subcategory)
    rows = []
    for item in items:
        catalog = _catalog_match(target, item["name"])
        if catalog:
            rows.append({"attributeId": catalog.id, "value": item["value"]})
            continue
        if target is None or not supplier_id:
            continue

        proposal = _private_proposal(
            supplier_id, target, attribute_id=item.get("id"), name=item["name"]
        )
        if proposal is None:
            attribute = TechnicalAttribute.objects.create(
                name_en=item["name"],
                name_fr=item["name"],
                attribute_type=item["attributeType"],
                attribute_level=2,
                unit=unit_for_symbol(item["unitSymbol"]),
                is_required=False,
                is_searchable=False,
                is_comparable=False,
            )
            TechnicalAttributeProposal.objects.create(
                attribute=attribute,
                supplier_id=supplier_id,
                category=target,
                status=TechnicalAttributeProposal.PENDING,
            )
        else:
            attribute = proposal.attribute
            if proposal.status == TechnicalAttributeProposal.PENDING:
                attribute.name_fr = item["name"]
                attribute.name_en = item["name"]
                attribute.attribute_type = item["attributeType"]
                attribute.unit = unit_for_symbol(item["unitSymbol"])
                attribute.save(
                    update_fields=["name_fr", "name_en", "attribute_type", "unit"]
                )
        rows.append({"attributeId": attribute.id, "value": item["value"]})
    return rows


def _is_private_attribute(attribute) -> bool:
    proposal = getattr(attribute, "proposal", None)
    if proposal is None:
        return False
    return proposal.status in PRIVATE_STATUSES


def existing_catalog_items(product) -> list[dict]:
    rows = product.attribute_values.select_related("attribute__proposal").all()
    return [
        {"attributeId": row.attribute_id, "value": _value_text(row)}
        for row in rows
        if not _is_private_attribute(row.attribute)
    ]


def existing_proposed_inputs(product) -> list[dict]:
    grouped: dict[int, dict] = {}
    order: list[int] = []
    rows = product.attribute_values.select_related(
        "attribute__proposal", "attribute__unit"
    ).all()
    for row in rows:
        attribute = row.attribute
        if not _is_private_attribute(attribute):
            continue
        if attribute.id not in grouped:
            order.append(attribute.id)
            grouped[attribute.id] = {
                "id": attribute.id,
                "name": attribute.name_fr or attribute.name_en,
                "attributeType": attribute.attribute_type,
                "unitSymbol": attribute.unit.symbol if attribute.unit_id else "",
                "value": [],
                "status": attribute.proposal.status,
            }
        grouped[attribute.id]["value"].append(_value_text(row))
    inputs = []
    for attribute_id in order:
        item = grouped[attribute_id]
        values = item.pop("value")
        if item["attributeType"] == "number":
            item["value"] = "|".join(values)
        else:
            item["value"] = values[-1] if values else ""
        inputs.append(item)
    return inputs


def proposed_attribute_payload(product) -> list[dict]:
    """Read model for the supplier form: private attributes and their values."""
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "attributeType": item["attributeType"],
            "unitSymbol": item["unitSymbol"] or None,
            "value": item["value"],
            "status": item["status"],
        }
        for item in existing_proposed_inputs(product)
    ]


def private_attribute_ids(product) -> set[int]:
    return {item["id"] for item in existing_proposed_inputs(product)}


def resolve_product_attribute_items(product, category, subcategory, technical_items, proposed_items):
    if technical_items is None:
        technical_items = existing_catalog_items(product)
    if proposed_items is None:
        proposed_items = [
            {key: value for key, value in item.items() if key != "status"}
            for item in existing_proposed_inputs(product)
        ]
    else:
        proposed_items = normalize_proposed_attributes(proposed_items)
    extra = upsert_supplier_proposed_attributes(
        product.supplier_id, category, subcategory, proposed_items
    )
    return list(technical_items or []) + extra


def drop_unused_supplier_proposals(supplier_id):
    if not supplier_id:
        return
    unused = TechnicalAttributeProposal.objects.filter(
        supplier_id=supplier_id,
        status=TechnicalAttributeProposal.PENDING,
    ).annotate(value_count=Count("attribute__product_values")).filter(value_count=0)
    attribute_ids = list(unused.values_list("attribute_id", flat=True))
    if attribute_ids:
        TechnicalAttribute.objects.filter(pk__in=attribute_ids).delete()


def _category_label(category) -> str:
    if category is None:
        return ""
    own = category.name_fr or category.name_en
    if category.parent_id:
        parent = category.parent
        parent_label = parent.name_fr or parent.name_en
        return f"{parent_label} — {own}"
    return own


def _sample_values(attribute) -> list[str]:
    samples = []
    for row in attribute.product_values.all():
        text = _value_text(row)
        if attribute.attribute_type == "boolean":
            text = "oui" if text == "true" else "non"
        if text and text not in samples:
            samples.append(text)
        if len(samples) == 4:
            break
    return samples


def serialize_proposal(proposal) -> dict:
    attribute = proposal.attribute
    product_count = getattr(proposal, "product_count", None)
    if product_count is None:
        product_count = attribute.product_values.values("product_id").distinct().count()
    return {
        "id": attribute.id,
        "name": attribute.name_fr or attribute.name_en,
        "attributeType": attribute.attribute_type,
        "unitSymbol": attribute.unit.symbol if attribute.unit_id else "",
        "status": proposal.status,
        "categoryId": proposal.category_id,
        "categoryLabel": _category_label(proposal.category),
        "supplierId": proposal.supplier_id,
        "supplierName": proposal.supplier.company_name if proposal.supplier_id else "",
        "productCount": product_count,
        "sampleValues": _sample_values(attribute),
    }


def proposal_queryset(status: str):
    return (
        TechnicalAttributeProposal.objects.filter(status=status)
        .select_related(
            "supplier",
            "category",
            "category__parent",
            "attribute",
            "attribute__unit",
        )
        .prefetch_related("attribute__product_values")
        .annotate(product_count=Count("attribute__product_values__product", distinct=True))
        .filter(product_count__gt=0)
        .order_by("-id")
    )


def _move_values(source, target):
    for row in list(source.product_values.all()):
        duplicate = ProductAttributeValue.objects.filter(
            product_id=row.product_id,
            attribute=target,
            num_value=row.num_value,
            txt_value=row.txt_value,
            bool_value=row.bool_value,
        ).exists()
        if duplicate:
            row.delete()
        else:
            row.attribute = target
            row.save(update_fields=["attribute"])


@transaction.atomic
def generalize_proposal(proposal, *, name: str, attribute_type: str, unit_symbol: str | None):
    if proposal.status == TechnicalAttributeProposal.GENERALIZED:
        raise ValueError("Cet attribut fait déjà partie du catalogue.")
    if attribute_type not in PROPOSED_TYPES:
        raise ValueError("Type d'attribut invalide.")
    name = _clean_name(name)
    if not name or len(name) > 150:
        raise ValueError("Le nom de la caractéristique est obligatoire.")

    attribute = proposal.attribute
    existing = _catalog_match(proposal.category, name)
    if existing and existing.id != attribute.id:
        _move_values(attribute, existing)
        attribute.delete()
        return existing

    attribute.name_fr = name
    attribute.name_en = name
    attribute.attribute_type = attribute_type
    attribute.attribute_level = 2
    attribute.is_searchable = True
    attribute.is_comparable = True
    if unit_symbol is not None:
        attribute.unit = unit_for_symbol(unit_symbol)
    attribute.save()
    CategoryAttribute.objects.get_or_create(
        category=proposal.category,
        attribute=attribute,
        defaults={"sort_order": 100, "is_required_for_publish": False},
    )
    proposal.status = TechnicalAttributeProposal.GENERALIZED
    proposal.reviewed_at = timezone.now()
    proposal.save(update_fields=["status", "reviewed_at"])
    return attribute


@transaction.atomic
def reject_proposal(proposal):
    if proposal.status == TechnicalAttributeProposal.GENERALIZED:
        raise ValueError("Un attribut du catalogue ne peut pas être refusé ici.")
    proposal.status = TechnicalAttributeProposal.REJECTED
    proposal.reviewed_at = timezone.now()
    proposal.save(update_fields=["status", "reviewed_at"])
    return proposal.attribute
