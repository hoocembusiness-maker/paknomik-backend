"""Level-1 and level-2 technical attributes for containers and labels.

Updates an existing category + characteristic instead of creating a second one.
Dimensions stays as it is. Mousse de protection keeps the characteristics
buyers already see on that type.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    ProductAttributeValue,
    TechnicalAttribute,
    Unit,
)

BOTTLES = "bouteilles-et-flacons"
CANS = "canettes-contenants-metalliques"
INDUSTRIAL = "emballage-industriel-protection"
LABELS = "etiquettes"
MOUSSE = "mousse-protection"
INDUSTRIAL_TYPES = (
    "bidon-plastique",
    "jerricane",
    "fut-industriel",
    "seau-industriel",
)
GLASS_TYPES = (
    "bouteille-verre",
    "flacon-pharmaceutique",
    "ampoule-emballage",
)

CONTAINER_SHAPES = (
    ("Cylindrique", "Cylindrique"),
    ("Carrée", "Carrée"),
    ("Rectangulaire", "Rectangulaire"),
    ("Ovale", "Ovale"),
    ("Conique", "Conique"),
    ("Autre", "Autre"),
)
CAN_SHAPES = tuple(item for item in CONTAINER_SHAPES if item[0] != "Conique")
LABEL_SHAPES = (
    ("Rectangle", "Rectangle"),
    ("Rond", "Rond"),
    ("Ovale", "Ovale"),
    ("Autre", "Autre"),
)
COLORS = (
    ("Transparent", "Transparent"),
    ("Ambré", "Ambré"),
    ("Bleu", "Bleu"),
    ("Vert", "Vert"),
    ("Blanc", "Blanc"),
    ("Autre", "Autre"),
)
GLASS_GRADES = (("Type I", "Type I"), ("Type II", "Type II"), ("Type III", "Type III"))
SUPPORTS = (("Papier", "Papier"), ("PP", "PP"), ("PET", "PET"), ("PVC", "PVC"))
ADHESIVES = (
    ("Permanent", "Permanent"),
    ("Repositionnable", "Repositionnable"),
    ("Congélation", "Congélation"),
)
PACKS = (("Rouleau", "Rouleau"), ("Feuille", "Feuille"), ("Planche", "Planche"))
OPENINGS = (
    ("Bonde", "Bonde"),
    ("Couvercle amovible", "Couvercle amovible"),
    ("Col étroit", "Col étroit"),
    ("Col large", "Col large"),
)

BOTTLE_L1 = ("Contenance", "Forme", "Couleur", "Usage pharmaceutique", "Dimensions")
CAN_L1 = ("Contenance", "Forme", "Usage alimentaire", "Dimensions")
INDUSTRIAL_L1 = (
    "Contenance",
    "Homologation UN (matières dangereuses)",
    "Dimensions",
)
LABEL_L1 = ("Laize", "Support", "Type d'adhésif", "Conditionnement", "Dimensions")


def _family(slug: str) -> Category | None:
    return Category.objects.filter(slug=slug, parent__isnull=True).first()


def _children(family: Category, slugs: tuple[str, ...]) -> list[Category]:
    found = {
        category.slug: category
        for category in Category.objects.filter(parent=family, slug__in=slugs)
    }
    missing = [slug for slug in slugs if slug not in found]
    if missing:
        raise ValueError(f"{family.slug} : types introuvables : {', '.join(missing)}")
    return [found[slug] for slug in slugs]


def _unit(symbol: str) -> Unit:
    unit = (
        Unit.objects.filter(symbol__iexact=symbol).order_by("id").first()
        or Unit.objects.filter(name__iexact=symbol).order_by("id").first()
    )
    if unit:
        return unit
    return Unit.objects.create(name=symbol, symbol=symbol)


def _linked(category: Category, name: str) -> list[TechnicalAttribute]:
    return list(
        TechnicalAttribute.objects.filter(
            category_attributes__category=category,
            name_fr__iexact=name,
        )
        .distinct()
        .order_by("attribute_level", "id")
    )


def _ensure(
    category: Category | None,
    names: tuple[str, ...],
    external_ids: tuple[str, ...],
    *,
    prefer_type: str | None = None,
):
    """Find the characteristic already used here, including after a rename."""
    found: list[TechnicalAttribute] = []
    if category is not None:
        for name in names:
            found.extend(_linked(category, name))
    if prefer_type:
        preferred = [attr for attr in found if attr.attribute_type == prefer_type]
        if preferred:
            found = preferred
    attr = found[0] if found else None
    if attr is None:
        attr = TechnicalAttribute.objects.filter(external_id__in=external_ids).order_by("id").first()
    if attr is None:
        attr = TechnicalAttribute.objects.create(
            external_id=external_ids[0],
            name_en=names[0],
            name_fr=names[0],
            attribute_type="text",
            attribute_level=2,
        )
    return attr


def _configure(attr: TechnicalAttribute, *, name_fr: str, name_en: str, attribute_type: str, level: int, unit: Unit | None, examples: str = ""):
    attr.name_fr = name_fr
    attr.name_en = name_en
    attr.attribute_type = attribute_type
    attr.attribute_level = level
    attr.unit = unit
    attr.is_required = False
    attr.is_searchable = level == 1
    attr.is_comparable = attribute_type in ("number", "choice")
    if examples:
        attr.examples = examples
    attr.save()


def _link(category: Category, attr: TechnicalAttribute, sort_order: int) -> CategoryAttribute:
    link, _ = CategoryAttribute.objects.update_or_create(
        category=category,
        attribute=attr,
        defaults={"sort_order": sort_order, "is_required_for_publish": False},
    )
    return link


def _set_options(link: CategoryAttribute, options: tuple[tuple[str, str], ...] | None):
    if not options:
        link.options.all().delete()
        return
    keep = set()
    for index, (value_en, value_fr) in enumerate(options, start=1):
        AttributeOption.objects.update_or_create(
            category_attribute=link,
            value_en=value_en,
            defaults={"value_fr": value_fr, "sort_order": index},
        )
        keep.add(value_en)
    link.options.exclude(value_en__in=keep).delete()


def _place(category: Category, attr: TechnicalAttribute, sort_order: int, options):
    link = _link(category, attr, sort_order)
    _set_options(link, options if attr.attribute_type == "choice" else None)
    return link


def _unlink_same_name(category: Category, keep: TechnicalAttribute):
    name = (keep.name_fr or "").casefold()
    extras = CategoryAttribute.objects.filter(category=category).exclude(attribute=keep).select_related("attribute")
    for link in extras:
        if (link.attribute.name_fr or "").casefold() != name:
            continue
        for row in ProductAttributeValue.objects.filter(attribute=link.attribute):
            duplicate = ProductAttributeValue.objects.filter(
                product_id=row.product_id,
                attribute=keep,
                num_value=row.num_value,
                txt_value=row.txt_value,
                bool_value=row.bool_value,
            ).exists()
            if duplicate:
                row.delete()
            else:
                row.attribute = keep
                row.save(update_fields=["attribute"])
        link.delete()


def _snapshot_onto(mousse: Category, attr: TechnicalAttribute):
    """Copy the current definition so a later edit does not change this type."""
    external_id = f"{attr.external_id or attr.pk}-mousse-preserve"
    clone = TechnicalAttribute.objects.filter(external_id=external_id).first()
    if clone is None:
        clone = TechnicalAttribute.objects.create(
            external_id=external_id,
            name_en=attr.name_en,
            name_fr=attr.name_fr,
            attribute_level=attr.attribute_level,
            attribute_type=attr.attribute_type,
            unit=attr.unit,
            is_required=attr.is_required,
            is_searchable=attr.is_searchable,
            is_comparable=attr.is_comparable,
            description=attr.description,
            examples=attr.examples,
        )
        source = CategoryAttribute.objects.filter(attribute=attr).exclude(category=mousse).first()
        dest = _link(mousse, clone, source.sort_order if source else 50)
        if source:
            for option in source.options.all():
                AttributeOption.objects.update_or_create(
                    category_attribute=dest,
                    value_en=option.value_en,
                    defaults={
                        "value_fr": option.value_fr,
                        "value_ar": option.value_ar,
                        "sort_order": option.sort_order,
                    },
                )
    else:
        _link(mousse, clone, 50)
    return clone


def _release_from_family(family: Category, mousse: Category | None, attr: TechnicalAttribute):
    """Keep Mousse seeing the old definition, then detach the shared family link."""
    linked = CategoryAttribute.objects.filter(category=family, attribute=attr).exists()
    if not linked:
        return
    if mousse is not None and attr.attribute_type == "choice":
        _snapshot_onto(mousse, attr)
    CategoryAttribute.objects.filter(category=family, attribute=attr).delete()


def _demote_extra_level1(family: Category, allowed: tuple[str, ...]):
    allowed_names = {name.casefold() for name in allowed}
    links = CategoryAttribute.objects.filter(
        category=family, attribute__attribute_level=1
    ).select_related("attribute")
    for link in links:
        attr = link.attribute
        if (attr.name_fr or "").casefold() in allowed_names:
            continue
        if (attr.external_id or "").startswith("core-packaging-"):
            continue
        shared = attr.category_attributes.exclude(
            Q(category=family) | Q(category__parent=family)
        ).exists()
        if shared:
            link.delete()
            continue
        attr.attribute_level = 2
        attr.is_searchable = False
        attr.save(update_fields=["attribute_level", "is_searchable"])


def visible_links(category: Category):
    from user.api.views import _category_attribute_links

    return _category_attribute_links(category)


def visible_signature(category: Category) -> frozenset:
    rows = []
    for link in visible_links(category):
        attr = link.attribute
        options = tuple(
            sorted(
                (option.value_fr or option.value_en, option.value_en)
                for option in link.options.all()
            )
        )
        rows.append(
            (
                attr.name_fr or attr.name_en,
                attr.attribute_level,
                attr.attribute_type,
                attr.unit.symbol if attr.unit_id else "",
                options,
            )
        )
    return frozenset(rows)


def _assert_level1(category: Category, allowed: tuple[str, ...]):
    level1 = [
        link.attribute
        for link in visible_links(category)
        if link.attribute.attribute_level == 1
    ]
    names = [attr.name_fr or attr.name_en for attr in level1]
    if len(level1) > 5:
        raise ValueError(f"{category.name_fr} a {len(level1)} caractéristiques de niveau 1 : {names}")
    allowed_names = {name.casefold() for name in allowed}
    extra = [name for name in names if name.casefold() not in allowed_names]
    if extra:
        raise ValueError(f"{category.name_fr} : niveau 1 inattendu : {extra}")
    folded = [name.casefold() for name in names]
    if len(folded) != len(set(folded)):
        raise ValueError(f"{category.name_fr} : caractéristique de niveau 1 en double")
    for link in visible_links(category):
        attr = link.attribute
        if attr.attribute_type == "choice" and not link.options.exists():
            raise ValueError(f"{category.name_fr} : « {attr.name_fr} » n'a pas d'options")
        if attr.attribute_type == "number" and not attr.unit_id:
            raise ValueError(f"{category.name_fr} : « {attr.name_fr} » n'a pas d'unité")


def _apply_bottles(family: Category, millilitre: Unit):
    contenance = _ensure(
        family,
        ("Contenance",),
        ("catalog-contenance-ml", "v4-06-L2-07"),
        prefer_type="number",
    )
    _configure(
        contenance,
        name_fr="Contenance",
        name_en="Capacity",
        attribute_type="number",
        level=1,
        unit=millilitre,
    )
    _place(family, contenance, 1, None)
    _unlink_same_name(family, contenance)

    shape = _ensure(family, ("Forme",), ("catalog-forme-contenant",))
    _configure(shape, name_fr="Forme", name_en="Shape", attribute_type="choice", level=1, unit=None)
    _place(family, shape, 2, CONTAINER_SHAPES)

    color = _ensure(family, ("Couleur",), ("catalog-couleur",))
    _configure(color, name_fr="Couleur", name_en="Colour", attribute_type="choice", level=1, unit=None)
    _place(family, color, 3, COLORS)

    pharma = _ensure(family, ("Usage pharmaceutique",), ("catalog-usage-pharmaceutique",))
    _configure(
        pharma,
        name_fr="Usage pharmaceutique",
        name_en="Pharmaceutical use",
        attribute_type="boolean",
        level=1,
        unit=None,
    )
    _place(family, pharma, 4, None)

    neck = _ensure(
        family,
        ("Col (filetage)", "Diamètre ou norme du col"),
        ("v4-06-L1-03", "catalog-norme-col"),
    )
    _configure(
        neck,
        name_fr="Diamètre ou norme du col",
        name_en="Neck finish",
        attribute_type="text",
        level=2,
        unit=None,
        examples="28/410, 24/410, PCO 1881",
    )
    _place(family, neck, 20, None)

    food = _ensure(family, ("Usage alimentaire",), ("catalog-usage-alimentaire-l2",))
    _configure(
        food,
        name_fr="Usage alimentaire",
        name_en="Food use",
        attribute_type="boolean",
        level=2,
        unit=None,
    )
    _place(family, food, 21, None)

    glass = _ensure(None, ("Type de verre",), ("catalog-type-verre",))
    _configure(
        glass,
        name_fr="Type de verre",
        name_en="Glass type",
        attribute_type="choice",
        level=2,
        unit=None,
    )
    for category in _children(family, GLASS_TYPES):
        _place(category, glass, 30, GLASS_GRADES)

    _demote_extra_level1(family, ("Contenance", "Forme", "Couleur", "Usage pharmaceutique"))
    return shape, contenance, food


def _apply_cans(family: Category, shape: TechnicalAttribute, contenance: TechnicalAttribute):
    _place(family, contenance, 1, None)
    _place(family, shape, 2, CAN_SHAPES)
    food = _ensure(family, ("Usage alimentaire",), ("catalog-usage-alimentaire-l1",))
    _configure(
        food,
        name_fr="Usage alimentaire",
        name_en="Food use",
        attribute_type="boolean",
        level=1,
        unit=None,
    )
    _place(family, food, 3, None)
    varnish = _ensure(family, ("Vernis intérieur",), ("catalog-vernis-interieur",))
    _configure(
        varnish,
        name_fr="Vernis intérieur",
        name_en="Internal lacquer",
        attribute_type="boolean",
        level=2,
        unit=None,
    )
    _place(family, varnish, 20, None)
    _demote_extra_level1(family, ("Contenance", "Forme", "Usage alimentaire"))


def _apply_industrial(family: Category, mousse: Category | None, litre: Unit, food_l2: TechnicalAttribute):
    contenance = _ensure(
        family,
        ("Contenance",),
        ("v4-10-L1-02", "catalog-contenance-l"),
        prefer_type="choice",
    )
    _release_from_family(family, mousse, contenance)
    _configure(
        contenance,
        name_fr="Contenance",
        name_en="Capacity",
        attribute_type="number",
        level=1,
        unit=litre,
    )

    approval = _ensure(
        family,
        ("Homologué ONU", "Homologation UN (matières dangereuses)"),
        ("v4-10-L1-03", "catalog-homologation-un"),
    )
    _release_from_family(family, mousse, approval)
    _configure(
        approval,
        name_fr="Homologation UN (matières dangereuses)",
        name_en="UN dangerous-goods approval",
        attribute_type="boolean",
        level=1,
        unit=None,
    )

    opening = _ensure(
        family,
        ("Type de fermeture", "Type d'ouverture"),
        ("v4-10-L1-04", "catalog-type-ouverture"),
    )
    _release_from_family(family, mousse, opening)
    _configure(
        opening,
        name_fr="Type d'ouverture",
        name_en="Opening type",
        attribute_type="choice",
        level=2,
        unit=None,
    )

    stackable = _ensure(None, ("Empilable",), ("catalog-empilable",))
    _configure(
        stackable,
        name_fr="Empilable",
        name_en="Stackable",
        attribute_type="boolean",
        level=2,
        unit=None,
    )

    matiere = TechnicalAttribute.objects.filter(
        category_attributes__category=family,
        name_fr__iexact="Matière",
        attribute_level=1,
    ).exclude(external_id="core-packaging-matiere").first()
    if matiere is not None:
        CategoryAttribute.objects.filter(category=family, attribute=matiere).delete()

    for category in _children(family, INDUSTRIAL_TYPES):
        _place(category, contenance, 1, None)
        _place(category, approval, 2, None)
        _place(category, opening, 20, OPENINGS)
        _place(category, food_l2, 21, None)
        _place(category, stackable, 22, None)
        _unlink_same_name(category, contenance)

    _demote_extra_level1(family, ())


def _apply_labels(family: Category, millimetre: Unit, food_l2: TechnicalAttribute):
    laize = _ensure(family, ("Largeur", "Laize"), ("v4-02-L2-01", "catalog-laize"))
    _configure(laize, name_fr="Laize", name_en="Web width", attribute_type="number", level=1, unit=millimetre)
    _place(family, laize, 1, None)

    support = _ensure(
        family,
        ("Support", "Matière"),
        ("v4-02-L1-01", "catalog-support-etiquette"),
    )
    if (support.external_id or "").startswith("core-packaging-"):
        support = _ensure(None, ("Support",), ("catalog-support-etiquette",))
    _configure(support, name_fr="Support", name_en="Face stock", attribute_type="choice", level=1, unit=None)
    _place(family, support, 2, SUPPORTS)

    adhesive = _ensure(
        family,
        ("Type d'adhésif",),
        ("v4-02-L1-04", "catalog-type-adhesif"),
    )
    _configure(
        adhesive,
        name_fr="Type d'adhésif",
        name_en="Adhesive type",
        attribute_type="choice",
        level=1,
        unit=None,
    )
    _place(family, adhesive, 3, ADHESIVES)

    pack = _ensure(family, ("Conditionnement",), ("catalog-conditionnement-etiquette",))
    _configure(
        pack,
        name_fr="Conditionnement",
        name_en="Supply format",
        attribute_type="choice",
        level=1,
        unit=None,
    )
    _place(family, pack, 4, PACKS)

    shape = _ensure(family, ("Forme",), ("v4-02-L1-02", "catalog-forme-etiquette"))
    if shape.external_id == "catalog-forme-contenant":
        shape = _ensure(None, ("Forme",), ("catalog-forme-etiquette",))
    _configure(shape, name_fr="Forme", name_en="Shape", attribute_type="choice", level=2, unit=None)
    _place(family, shape, 20, LABEL_SHAPES)

    mandrin = _ensure(
        family,
        ("Diamètre mandrin",),
        ("v4-02-L2-03", "catalog-diametre-mandrin"),
    )
    _configure(
        mandrin,
        name_fr="Diamètre mandrin",
        name_en="Core diameter",
        attribute_type="number",
        level=2,
        unit=millimetre,
    )
    _place(family, mandrin, 21, None)
    _place(family, food_l2, 22, None)
    _demote_extra_level1(family, ("Laize", "Support", "Type d'adhésif", "Conditionnement"))


@transaction.atomic
def apply_container_and_label_attributes() -> dict:
    families = {slug: _family(slug) for slug in (BOTTLES, CANS, INDUSTRIAL, LABELS)}
    missing = [slug for slug, family in families.items() if family is None]
    if missing:
        raise ValueError(f"Familles introuvables : {', '.join(missing)}")

    mousse = Category.objects.filter(slug=MOUSSE, parent=families[INDUSTRIAL]).first()
    mousse_before = visible_signature(mousse) if mousse is not None else None
    dimensions = TechnicalAttribute.objects.filter(external_id="core-packaging-dimensions").first()
    dimensions_before = None
    if dimensions is not None:
        dimensions_before = (
            dimensions.name_fr,
            dimensions.name_en,
            dimensions.attribute_type,
            dimensions.attribute_level,
            dimensions.unit_id,
            dimensions.external_id,
        )

    millilitre = _unit("ml")
    litre = _unit("L")
    millimetre = _unit("mm")

    shape, contenance, food_l2 = _apply_bottles(families[BOTTLES], millilitre)
    _apply_cans(families[CANS], shape, contenance)
    _apply_industrial(families[INDUSTRIAL], mousse, litre, food_l2)
    _apply_labels(families[LABELS], millimetre, food_l2)

    if dimensions is not None and dimensions_before is not None:
        dimensions.refresh_from_db()
        dimensions_after = (
            dimensions.name_fr,
            dimensions.name_en,
            dimensions.attribute_type,
            dimensions.attribute_level,
            dimensions.unit_id,
            dimensions.external_id,
        )
        if dimensions_after != dimensions_before:
            raise ValueError("Le champ Dimensions a été modifié.")

    if mousse is not None and visible_signature(mousse) != mousse_before:
        raise ValueError("Mousse de protection a changé.")

    for category in families[BOTTLES].children.all():
        _assert_level1(category, BOTTLE_L1)
    for category in families[CANS].children.all():
        _assert_level1(category, CAN_L1)
    for category in _children(families[INDUSTRIAL], INDUSTRIAL_TYPES):
        _assert_level1(category, INDUSTRIAL_L1)
    for category in families[LABELS].children.all():
        _assert_level1(category, LABEL_L1)

    return {"families": list(families)}
