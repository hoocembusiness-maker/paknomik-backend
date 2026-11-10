"""Boîte compartimentée type, layout attribute, and Pro Box dimensions.

Idempotent. Safe to re-run.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    ProductType,
    Supplier,
    TechnicalAttribute,
    Unit,
)

COMPARTMENT_TYPE_SLUG = "boite-compartimentee"
PLATEAU_TYPE_SLUG = "boite-plateau"
FAMILY_SLUG = "boites-et-cartons"

REPARTITION_EXTERNAL_ID = "v4-boite-compartiment-L1-repartition"
LENGTH_EXTERNAL_ID = "v4-boite-compartiment-L1-longueur"
WIDTH_EXTERNAL_ID = "v4-boite-compartiment-L1-largeur"
HEIGHT_EXTERNAL_ID = "v4-boite-compartiment-L1-hauteur"
COMBINED_EXTERNAL_ID = "core-packaging-dimensions"

REPARTITION_OPTIONS = (
    ("2x2", "2x2"),
    ("2x3", "2x3"),
    ("2x4", "2x4"),
    ("2x5", "2x5"),
    ("3x3", "3x3"),
    ("3x4", "3x4"),
    ("4x4", "4x4"),
    ("3 alignées", "3 alignées"),
    ("4 alignées", "4 alignées"),
    ("5 alignées", "5 alignées"),
    ("1 + 2 demi", "1 + 2 demi"),
    ("1 + 3", "1 + 3"),
)

PROBOX_SUPPLIER_Q = (
    Q(company_name__icontains="pro box")
    | Q(company_name__icontains="probox")
    | Q(company_name__icontains="pro emballage")
    | Q(email__iexact="smi.kecar24@gmail.com")
)


@dataclass(frozen=True)
class ProBoxSpec:
    keys: tuple[str, ...]
    length_cm: float
    width_cm: float
    height_cm: float
    layout: str | None = None
    exclude: tuple[str, ...] = ()
    name_with_dims: str | None = None


# Catalogue Pro Emballage / Pro Box. S and DD dimensions follow cell size on the listing.
PROBOX_SPECS = (
    ProBoxSpec(("modèle d — 12", "modele d - 12", "modèle d - 12"), 12, 8, 4),
    ProBoxSpec(("modèle f",), 8, 8, 4),
    ProBoxSpec(("c+",), 25, 10, 4, layout="2x5"),
    ProBoxSpec(("modèle c",), 25, 10, 4, exclude=("c+",)),
    ProBoxSpec(("e+",), 16, 12, 4, layout="3x3"),
    ProBoxSpec(("modèle a",), 21, 21, 4),
    ProBoxSpec(("modèle b",), 16, 16, 4),
    ProBoxSpec(("modèle d — 26", "modele d - 26", "26 × 5", "26 x 5"), 26, 5, 4),
    ProBoxSpec(("modèle g",), 8, 4, 3.5),
    ProBoxSpec(
        ("modèle s",),
        12,
        8,
        4,
        layout="2x3",
        exclude=("allong", "dd"),
        name_with_dims="Boîte compartimentée Modèle S — 12 × 8 × 4 cm",
    ),
    ProBoxSpec(
        ("modèle dd", "modele dd"),
        20,
        4,
        4,
        layout="4 alignées",
        name_with_dims="Boîte compartimentée allongée Modèle DD — 20 × 4 × 4 cm",
    ),
)


def _fold(text: str) -> str:
    return (
        (text or "")
        .casefold()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("×", "x")
        .replace("—", "-")
        .replace("–", "-")
    )


def _matches(name: str, spec: ProBoxSpec) -> bool:
    folded = _fold(name)
    if any(_fold(token) in folded for token in spec.exclude):
        return False
    return any(_fold(token) in folded for token in spec.keys)


def _cm_label(spec: ProBoxSpec) -> str:
    def fmt(value: float) -> str:
        text = f"{value:g}".replace(".", ",")
        return text

    return f"{fmt(spec.length_cm)} × {fmt(spec.width_cm)} × {fmt(spec.height_cm)} cm"


def _cm_unit() -> Unit:
    unit = Unit.objects.filter(symbol="cm").first()
    if unit:
        return unit
    unit, _ = Unit.objects.get_or_create(name="cm", defaults={"symbol": "cm"})
    if not unit.symbol:
        unit.symbol = "cm"
        unit.save(update_fields=["symbol"])
    return unit


def ensure_compartiment_category() -> Category:
    from user.taxonomy_v4 import apply_taxonomy_v4

    apply_taxonomy_v4()
    category = Category.objects.filter(
        slug=COMPARTMENT_TYPE_SLUG, parent__slug=FAMILY_SLUG
    ).first()
    if category:
        return category
    family = Category.objects.get(slug=FAMILY_SLUG, parent__isnull=True)
    category, _ = Category.objects.update_or_create(
        slug=COMPARTMENT_TYPE_SLUG,
        defaults={
            "parent": family,
            "level": 2,
            "name_fr": "Boîte compartimentée",
            "name_en": "Compartment box",
            "sort_order": 65,
        },
    )
    return category


def _upsert_attribute(
    *,
    external_id: str,
    name_en: str,
    name_fr: str,
    attribute_type: str,
    sort_order: int,
    searchable: bool,
    unit: Unit | None = None,
) -> TechnicalAttribute:
    attribute, _ = TechnicalAttribute.objects.update_or_create(
        external_id=external_id,
        defaults={
            "name_en": name_en,
            "name_fr": name_fr,
            "attribute_level": 1,
            "attribute_type": attribute_type,
            "unit": unit,
            "is_required": False,
            "is_searchable": searchable,
            "is_comparable": False,
        },
    )
    return attribute


def _link(category: Category, attribute: TechnicalAttribute, sort_order: int) -> CategoryAttribute:
    link, _ = CategoryAttribute.objects.update_or_create(
        category=category,
        attribute=attribute,
        defaults={
            "sort_order": sort_order,
            "is_required_for_publish": False,
        },
    )
    return link


def seed_boite_compartiment_attributes() -> dict[str, int]:
    compartment = ensure_compartiment_category()
    plateau = Category.objects.filter(slug=PLATEAU_TYPE_SLUG, parent__slug=FAMILY_SLUG).first()
    unit = _cm_unit()

    length = _upsert_attribute(
        external_id=LENGTH_EXTERNAL_ID,
        name_en="Length",
        name_fr="Longueur",
        attribute_type="number",
        sort_order=10,
        searchable=True,
        unit=unit,
    )
    width = _upsert_attribute(
        external_id=WIDTH_EXTERNAL_ID,
        name_en="Width",
        name_fr="Largeur",
        attribute_type="number",
        sort_order=11,
        searchable=True,
        unit=unit,
    )
    height = _upsert_attribute(
        external_id=HEIGHT_EXTERNAL_ID,
        name_en="Height",
        name_fr="Hauteur",
        attribute_type="number",
        sort_order=12,
        searchable=True,
        unit=unit,
    )
    layout = _upsert_attribute(
        external_id=REPARTITION_EXTERNAL_ID,
        name_en="Compartment layout",
        name_fr="Répartition des compartiments",
        attribute_type="choice",
        sort_order=20,
        searchable=True,
    )

    from user.core_packaging_attributes import seed_core_packaging_attributes

    seed_core_packaging_attributes()

    for category in filter(None, (compartment, plateau)):
        _link(category, length, 10)
        _link(category, width, 11)
        _link(category, height, 12)
    link = _link(compartment, layout, 20)
    for index, (value_en, value_fr) in enumerate(REPARTITION_OPTIONS, start=1):
        AttributeOption.objects.update_or_create(
            category_attribute=link,
            value_en=value_en,
            defaults={"value_fr": value_fr, "sort_order": index},
        )

    try:
        from user.catalog_seed import seed_catalog_taxonomy
        from user.catalog_taxonomy import SchemaError

        seed_catalog_taxonomy()
    except SchemaError:
        pass

    return {
        "type_id": compartment.id,
        "options": AttributeOption.objects.filter(category_attribute=link).count(),
    }


def _set_num(product: Product, attribute: TechnicalAttribute, value: float) -> None:
    ProductAttributeValue.objects.filter(product=product, attribute=attribute).delete()
    ProductAttributeValue.objects.create(
        product=product,
        attribute=attribute,
        num_value=float(value),
    )


def _set_text(product: Product, attribute: TechnicalAttribute, value: str) -> None:
    ProductAttributeValue.objects.filter(product=product, attribute=attribute).delete()
    ProductAttributeValue.objects.create(
        product=product,
        attribute=attribute,
        txt_value=value,
    )


def _spec_for(name: str) -> ProBoxSpec | None:
    for spec in PROBOX_SPECS:
        if _matches(name, spec):
            return spec
    return None


def probox_suppliers():
    return Supplier.objects.filter(PROBOX_SUPPLIER_Q)


def update_probox_products() -> dict[str, int]:
    stats = {"suppliers": 0, "updated": 0, "skipped": 0}
    length = TechnicalAttribute.objects.filter(external_id=LENGTH_EXTERNAL_ID).first()
    width = TechnicalAttribute.objects.filter(external_id=WIDTH_EXTERNAL_ID).first()
    height = TechnicalAttribute.objects.filter(external_id=HEIGHT_EXTERNAL_ID).first()
    layout = TechnicalAttribute.objects.filter(external_id=REPARTITION_EXTERNAL_ID).first()
    combined = TechnicalAttribute.objects.filter(external_id=COMBINED_EXTERNAL_ID).first()
    compartment = Category.objects.filter(slug=COMPARTMENT_TYPE_SLUG).first()
    family = Category.objects.filter(slug=FAMILY_SLUG, parent__isnull=True).first()
    catalog_type = ProductType.objects.filter(slug=COMPARTMENT_TYPE_SLUG).first()

    if not all((length, width, height)):
        return stats

    for supplier in probox_suppliers():
        stats["suppliers"] += 1
        for product in Product.objects.filter(supplier=supplier):
            spec = _spec_for(product.name)
            if spec is None:
                stats["skipped"] += 1
                continue
            _set_num(product, length, spec.length_cm)
            _set_num(product, width, spec.width_cm)
            _set_num(product, height, spec.height_cm)
            if combined:
                _set_text(product, combined, _cm_label(spec))
            if spec.layout and layout:
                _set_text(product, layout, spec.layout)
            if spec.layout and compartment:
                product.subcategory = compartment
                if family:
                    product.category = family
            if spec.layout and catalog_type:
                product.product_type = catalog_type
                attrs = dict(product.attributes or {})
                attrs["dimensions"] = _cm_label(spec)
                attrs["repartition_compartiments"] = spec.layout
                attrs.setdefault("matiere", "carton compact")
                product.attributes = attrs
            if spec.name_with_dims and spec.name_with_dims != product.name:
                product.name = spec.name_with_dims
            product.save()
            stats["updated"] += 1
    return stats


def seed_boite_compartiment() -> dict[str, int]:
    created = seed_boite_compartiment_attributes()
    updated = update_probox_products()
    return {**created, **updated}


__all__ = [
    "COMPARTMENT_TYPE_SLUG",
    "PROBOX_SPECS",
    "REPARTITION_OPTIONS",
    "seed_boite_compartiment",
    "seed_boite_compartiment_attributes",
    "update_probox_products",
]
