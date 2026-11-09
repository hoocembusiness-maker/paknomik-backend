"""Minimal technical attributes for packaging types.

Every marketplace packaging subcategory should expose at least:
Matière, Dimensions, Fermeture. Types that already have equivalent fields
keep their existing attributes; only the missing cores are linked.
"""

from __future__ import annotations

from dataclasses import dataclass

from user.models import Category, CategoryAttribute, TechnicalAttribute

# Physical packaging families. Services, demo and review dump are excluded.
PACKAGING_FAMILY_SLUGS = frozenset(
    {
        "boites-et-cartons",
        "sacs-et-pochettes",
        "bouteilles-et-flacons",
        "pots-gobelets-contenants",
        "canettes-contenants-metalliques",
        "tubes",
        "etiquettes",
        "fermetures-accessoires",
        "films-materiaux-souples",
        "papiers-emballage",
        "emballage-industriel-protection",
    }
)

CORE_SPECS = (
    {
        "key": "matiere",
        "external_id": "core-packaging-matiere",
        "name_en": "Material",
        "name_fr": "Matière",
        "sort_order": 10,
        "description": "Matière principale de l'emballage (PET, carton, verre, aluminium).",
        "examples": "PET, PP, carton ondulé, verre, aluminium",
    },
    {
        "key": "dimensions",
        "external_id": "core-packaging-dimensions",
        "name_en": "Dimensions",
        "name_fr": "Dimensions",
        "sort_order": 20,
        "description": "Cotes utiles (L x l x H, diamètre, laize).",
        "examples": "120 x 80 x 40 mm ; diamètre 50 mm ; laize 600 mm",
    },
    {
        "key": "fermeture",
        "external_id": "core-packaging-fermeture",
        "name_en": "Closure",
        "name_fr": "Fermeture",
        "sort_order": 30,
        "description": "Mode de fermeture ou accessoire associé.",
        "examples": "Bouchon à vis, flip-top, opercule, thermosoudure, zip",
    },
)

_MATIERE_NEEDLES = (
    "matéri",
    "materi",
    "matière",
    "matiere",
    "material",
    "polymère",
    "polymere",
    "polymer",
)
_DIMENSION_NEEDLES = (
    "dimension",
    "longueur",
    "largeur",
    "hauteur",
    "diamètre",
    "diameter",
    "taille",
    "epaisseur",
    "thickness",
    "contenance",
    "volume",
    "laize",
    "width",
    "length",
    "height",
    "depth",
)
_FERMETURE_NEEDLES = (
    "fermeture",
    "closure",
    "bouchon",
    "couvercle",
    "opercule",
    "capsul",
    "capuchon",
    "zip",
    "thermosoud",
    "scell",
)


@dataclass
class CoreSeedStats:
    categories_scanned: int = 0
    attributes_ensured: int = 0
    links_created: int = 0
    links_existing: int = 0
    skipped_complete: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "categories_scanned": self.categories_scanned,
            "attributes_ensured": self.attributes_ensured,
            "links_created": self.links_created,
            "links_existing": self.links_existing,
            "skipped_complete": self.skipped_complete,
        }


def _name_fold(attribute: TechnicalAttribute) -> str:
    return f"{attribute.name_fr or ''} {attribute.name_en or ''}".casefold()


def _has_needle(names: list[str], needles: tuple[str, ...]) -> bool:
    return any(any(needle in name for needle in needles) for name in names)


def category_core_coverage(category: Category) -> dict[str, bool]:
    attrs = [
        link.attribute
        for link in CategoryAttribute.objects.filter(category=category).select_related(
            "attribute"
        )
    ]
    names = [_name_fold(attr) for attr in attrs]
    external_ids = {attr.external_id for attr in attrs if attr.external_id}
    return {
        "matiere": "core-packaging-matiere" in external_ids
        or _has_needle(names, _MATIERE_NEEDLES),
        "dimensions": "core-packaging-dimensions" in external_ids
        or _has_needle(names, _DIMENSION_NEEDLES),
        "fermeture": "core-packaging-fermeture" in external_ids
        or _has_needle(names, _FERMETURE_NEEDLES),
    }


def packaging_subcategories():
    return (
        Category.objects.filter(parent__isnull=False, parent__slug__in=PACKAGING_FAMILY_SLUGS)
        .select_related("parent")
        .order_by("parent__sort_order", "sort_order", "name_fr")
    )


def ensure_core_attributes() -> dict[str, TechnicalAttribute]:
    ensured: dict[str, TechnicalAttribute] = {}
    for spec in CORE_SPECS:
        attribute, _ = TechnicalAttribute.objects.update_or_create(
            external_id=spec["external_id"],
            defaults={
                "name_en": spec["name_en"],
                "name_fr": spec["name_fr"],
                "attribute_level": 2,
                "attribute_type": "text",
                "is_required": False,
                "is_searchable": False,
                "is_comparable": False,
                "description": spec["description"],
                "examples": spec["examples"],
            },
        )
        ensured[spec["key"]] = attribute
    return ensured


def seed_core_packaging_attributes() -> CoreSeedStats:
    stats = CoreSeedStats()
    attributes = ensure_core_attributes()
    stats.attributes_ensured = len(attributes)

    for category in packaging_subcategories():
        stats.categories_scanned += 1
        coverage = category_core_coverage(category)
        missing = [key for key, present in coverage.items() if not present]
        if not missing:
            stats.skipped_complete += 1
            continue
        for key in missing:
            attribute = attributes[key]
            spec = next(item for item in CORE_SPECS if item["key"] == key)
            _link, created = CategoryAttribute.objects.get_or_create(
                category=category,
                attribute=attribute,
                defaults={
                    "sort_order": spec["sort_order"],
                    "is_required_for_publish": False,
                },
            )
            if created:
                stats.links_created += 1
            else:
                stats.links_existing += 1
    return stats


def preview_core_packaging_attributes() -> CoreSeedStats:
    """Count missing cores without writing."""
    stats = CoreSeedStats(attributes_ensured=len(CORE_SPECS))
    for category in packaging_subcategories():
        stats.categories_scanned += 1
        coverage = category_core_coverage(category)
        missing = [key for key, present in coverage.items() if not present]
        if not missing:
            stats.skipped_complete += 1
            continue
        stats.links_created += len(missing)
    return stats
