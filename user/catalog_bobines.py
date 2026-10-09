"""
Bobines category tree and technical attribute definitions (v5 catalog extension).

Stable slugs: bobines, bobine_papier, bobine_aluminium, bobine_pvc,
bobine_film_plastique, bobine_laminee_composite, bobine_speciale.
"""

from __future__ import annotations

from typing import Any

PARENT_SLUG = "bobines"

PARENT_CATEGORY: dict[str, Any] = {
    "external_id": "v5-cat-bobines",
    "slug": PARENT_SLUG,
    "sort_order": 1100,
    "level": 1,
    "name_en": "Reels",
    "name_fr": "Bobines",
    "name_ar": "بكرات",
    "plain_description": (
        "Bobines de papier, aluminium, PVC, films plastiques, laminés et spéciales."
    ),
    "is_new_in_v2": True,
}

SUBCATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "external_id": "v5-sub-bobine-papier",
        "slug": "bobine_papier",
        "sort_order": 1101,
        "name_en": "Paper reel",
        "name_fr": "Bobine de papier",
        "plain_description": "Bobines de papier kraft, couché, offset et spécialités.",
    },
    {
        "external_id": "v5-sub-bobine-aluminium",
        "slug": "bobine_aluminium",
        "sort_order": 1102,
        "name_en": "Aluminium reel",
        "name_fr": "Bobine d'aluminium",
        "plain_description": "Bobines et feuilles d'aluminium pour emballage et laminage.",
    },
    {
        "external_id": "v5-sub-bobine-pvc",
        "slug": "bobine_pvc",
        "sort_order": 1103,
        "name_en": "PVC reel",
        "name_fr": "Bobine de PVC",
        "plain_description": "Films PVC souples, rigides et thermorétractables en bobine.",
    },
    {
        "external_id": "v5-sub-bobine-film-plastique",
        "slug": "bobine_film_plastique",
        "sort_order": 1104,
        "name_en": "Plastic film reel",
        "name_fr": "Bobine de film plastique",
        "plain_description": "Films PE, PP, PET et autres polymères en bobine.",
    },
    {
        "external_id": "v5-sub-bobine-laminee-composite",
        "slug": "bobine_laminee_composite",
        "sort_order": 1105,
        "name_en": "Laminated / composite reel",
        "name_fr": "Bobine laminée / composite",
        "plain_description": "Structures laminées multicouches pour barrière et scellage.",
    },
    {
        "external_id": "v5-sub-bobine-speciale",
        "slug": "bobine_speciale",
        "sort_order": 1106,
        "name_en": "Special reel",
        "name_fr": "Bobine spéciale",
        "plain_description": "Bobines sur mesure et matières atypiques.",
    },
)

PAPER_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Kraft", "Kraft"),
    ("Blanc", "Blanc"),
    ("Couché", "Couché"),
    ("Offset", "Offset"),
    ("Coloré", "Coloré"),
    ("Recyclé", "Recyclé"),
    ("Alimentaire", "Alimentaire"),
    ("Glassine", "Glassine"),
    ("Sulfurisé / cuisson", "Sulfurisé / cuisson"),
    ("Extensible", "Extensible"),
    ("Papier pour sacs", "Papier pour sacs"),
    ("Papier d'emballage", "Papier d'emballage"),
    ("Autre", "Autre"),
)

ALUMINIUM_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Feuille aluminium standard", "Feuille aluminium standard"),
    ("Aluminium alimentaire", "Aluminium alimentaire"),
    ("Aluminium imprimable", "Aluminium imprimable"),
    ("Aluminium laqué", "Aluminium laqué"),
    ("Aluminium pour laminage", "Aluminium pour laminage"),
    ("Autre", "Autre"),
)

PVC_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("PVC souple", "PVC souple"),
    ("PVC rigide", "PVC rigide"),
    ("PVC transparent", "PVC transparent"),
    ("PVC opaque", "PVC opaque"),
    ("PVC imprimable", "PVC imprimable"),
    ("PVC thermorétractable", "PVC thermorétractable"),
    ("Autre", "Autre"),
)

POLYMER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("PE", "PE"),
    ("PP", "PP"),
    ("PET", "PET"),
    ("PA / Nylon", "PA / Nylon"),
    ("Autre", "Autre"),
)

POLYMER_SUBTYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("LDPE", "LDPE"),
    ("LLDPE", "LLDPE"),
    ("HDPE", "HDPE"),
    ("CPP", "CPP"),
    ("BOPP", "BOPP"),
    ("PET transparent", "PET transparent"),
    ("PET métallisé", "PET métallisé"),
    ("Autre", "Autre"),
)

COMPOSITE_STRUCTURE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Papier / PE", "Papier / PE"),
    ("Papier / Aluminium", "Papier / Aluminium"),
    ("Papier / PE / Aluminium", "Papier / PE / Aluminium"),
    ("PET / PE", "PET / PE"),
    ("PET / Aluminium / PE", "PET / Aluminium / PE"),
    ("BOPP / PE", "BOPP / PE"),
    ("BOPP / CPP", "BOPP / CPP"),
    ("PA / PE", "PA / PE"),
    ("Aluminium / PE", "Aluminium / PE"),
    ("PET / PA / PE", "PET / PA / PE"),
    ("Autre structure", "Autre structure"),
)

FINISH_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Mat", "Mat"),
    ("Brillant", "Brillant"),
    ("Couché", "Couché"),
    ("Non couché", "Non couché"),
    ("Laqué", "Laqué"),
    ("Autre", "Autre"),
)

COLOR_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Brun naturel", "Brun naturel"),
    ("Blanc", "Blanc"),
    ("Transparent", "Transparent"),
    ("Coloré", "Coloré"),
    ("Métallisé", "Métallisé"),
    ("Autre", "Autre"),
)


def _num(
    external_id: str,
    name_en: str,
    name_fr: str,
    unit: str,
    sort_order: int,
    *,
    level: int = 1,
    searchable: bool = True,
    required: bool = False,
    comparable: bool = True,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "name_en": name_en,
        "name_fr": name_fr,
        "attribute_level": level,
        "attribute_type": "number",
        "unit_symbol": unit,
        "is_required": required,
        "is_searchable": searchable,
        "is_comparable": comparable,
        "sort_order": sort_order,
    }


def _choice(
    external_id: str,
    name_en: str,
    name_fr: str,
    options: tuple[tuple[str, str], ...],
    sort_order: int,
    *,
    level: int = 1,
    searchable: bool = True,
    required: bool = False,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "name_en": name_en,
        "name_fr": name_fr,
        "attribute_level": level,
        "attribute_type": "choice",
        "unit_symbol": None,
        "is_required": required,
        "is_searchable": searchable,
        "is_comparable": True,
        "sort_order": sort_order,
        "options": list(options),
    }


def _bool(
    external_id: str,
    name_en: str,
    name_fr: str,
    sort_order: int,
    *,
    level: int = 1,
    searchable: bool = True,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "name_en": name_en,
        "name_fr": name_fr,
        "attribute_level": level,
        "attribute_type": "boolean",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": searchable,
        "is_comparable": True,
        "sort_order": sort_order,
    }


def _text(
    external_id: str,
    name_en: str,
    name_fr: str,
    sort_order: int,
    *,
    level: int = 2,
    searchable: bool = False,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "name_en": name_en,
        "name_fr": name_fr,
        "attribute_level": level,
        "attribute_type": "text",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": searchable,
        "is_comparable": False,
        "sort_order": sort_order,
    }


def _common_dimensions(sort_start: int = 20) -> list[dict[str, Any]]:
    return [
        _num(
            "v5-bobine-attr-width",
            "Width",
            "Largeur ou laize",
            "mm",
            sort_start,
            searchable=True,
        ),
        _num(
            "v5-bobine-attr-length",
            "Length",
            "Longueur",
            "m",
            sort_start + 1,
        ),
        _num(
            "v5-bobine-attr-thickness",
            "Thickness",
            "Épaisseur",
            "µm",
            sort_start + 2,
            searchable=True,
        ),
        _num(
            "v5-bobine-attr-weight",
            "Weight",
            "Poids",
            "kg",
            sort_start + 3,
            level=2,
            searchable=False,
        ),
        _num(
            "v5-bobine-attr-outer-diameter",
            "Outer diameter",
            "Diamètre extérieur",
            "mm",
            sort_start + 4,
            level=2,
        ),
        _num(
            "v5-bobine-attr-inner-diameter",
            "Inner diameter / core",
            "Diamètre intérieur ou mandrin",
            "mm",
            sort_start + 5,
            level=2,
        ),
        _choice(
            "v5-bobine-attr-color",
            "Color",
            "Couleur",
            COLOR_OPTIONS,
            sort_start + 6,
            searchable=True,
        ),
        _choice(
            "v5-bobine-attr-finish",
            "Finish",
            "Finition",
            FINISH_OPTIONS,
            sort_start + 7,
            level=2,
            searchable=True,
        ),
        _bool(
            "v5-bobine-attr-food-grade",
            "Food contact",
            "Usage alimentaire",
            sort_start + 8,
            searchable=True,
        ),
        _bool(
            "v5-bobine-attr-pharma",
            "Pharmaceutical use",
            "Usage pharmaceutique",
            sort_start + 9,
            searchable=True,
        ),
        _bool(
            "v5-bobine-attr-printable",
            "Printable",
            "Imprimabilité",
            sort_start + 10,
            searchable=True,
        ),
        _text(
            "v5-bobine-attr-tech-sheet-note",
            "Technical sheet (PDF)",
            "Fiche technique PDF (référence)",
            sort_start + 11,
            level=2,
        ),
    ]


ATTRIBUTES_BY_SUBCATEGORY_SLUG: dict[str, list[dict[str, Any]]] = {
    "bobine_papier": [
        _choice(
            "v5-bobine-papier-type",
            "Paper type",
            "Type de papier",
            PAPER_TYPE_OPTIONS,
            1,
            required=True,
            searchable=True,
        ),
        *_common_dimensions(20),
        _num(
            "v5-bobine-papier-grammage",
            "Grammage",
            "Grammage",
            "g/m²",
            15,
            searchable=True,
        ),
        _num("v5-bobine-papier-whiteness", "Whiteness", "Blancheur", "%", 16, level=2),
        _text("v5-bobine-papier-coating", "Coating", "Couchage", 17, level=2),
        _text(
            "v5-bobine-papier-tensile",
            "Tensile strength",
            "Résistance à la traction",
            18,
            level=2,
        ),
        _text(
            "v5-bobine-papier-tear",
            "Tear resistance",
            "Résistance à la déchirure",
            19,
            level=2,
        ),
        _text(
            "v5-bobine-papier-burst",
            "Burst strength",
            "Résistance à l'éclatement",
            20,
            level=2,
        ),
        _bool(
            "v5-bobine-papier-water-resist",
            "Water resistance",
            "Résistance à l'eau",
            21,
            level=2,
            searchable=False,
        ),
        _bool("v5-bobine-papier-fsc", "FSC certified", "Certification FSC", 22, level=2),
        _bool("v5-bobine-papier-pefc", "PEFC certified", "Certification PEFC", 23, level=2),
    ],
    "bobine_aluminium": [
        _choice(
            "v5-bobine-alu-type",
            "Aluminium type",
            "Type d'aluminium",
            ALUMINIUM_TYPE_OPTIONS,
            1,
            required=True,
            searchable=True,
        ),
        *_common_dimensions(20),
        _text("v5-bobine-alu-alloy", "Alloy", "Alliage", 15, level=2),
        _text(
            "v5-bobine-alu-temper",
            "Metallurgical temper",
            "État métallurgique (temper)",
            16,
            level=2,
        ),
        _text(
            "v5-bobine-alu-surface",
            "Surface finish",
            "Finition de surface",
            17,
            level=2,
        ),
        _bool("v5-bobine-alu-lacquered", "Lacquered", "Laqué", 18, level=2),
        _text(
            "v5-bobine-alu-mechanical",
            "Mechanical properties",
            "Propriétés mécaniques",
            19,
            level=2,
        ),
    ],
    "bobine_pvc": [
        _choice(
            "v5-bobine-pvc-type",
            "PVC type",
            "Type de PVC",
            PVC_TYPE_OPTIONS,
            1,
            required=True,
            searchable=True,
        ),
        *_common_dimensions(20),
        _bool("v5-bobine-pvc-transparent", "Transparent", "Transparence", 15, level=2),
        _text("v5-bobine-pvc-surface", "Surface treatment", "Traitement de surface", 16),
        _text(
            "v5-bobine-pvc-tensile",
            "Tensile strength",
            "Résistance à la traction",
            17,
            level=2,
        ),
        _text("v5-bobine-pvc-elongation", "Elongation", "Allongement", 18, level=2),
        _text(
            "v5-bobine-pvc-chemical",
            "Chemical resistance",
            "Résistance chimique",
            19,
            level=2,
        ),
        _text(
            "v5-bobine-pvc-temp",
            "Operating temperature",
            "Température d'utilisation",
            20,
            level=2,
        ),
        _bool(
            "v5-bobine-pvc-shrink",
            "Shrinkable",
            "Rétractabilité",
            21,
            searchable=True,
        ),
    ],
    "bobine_film_plastique": [
        _choice(
            "v5-bobine-film-polymer",
            "Polymer",
            "Polymère",
            POLYMER_OPTIONS,
            1,
            required=True,
            searchable=True,
        ),
        _choice(
            "v5-bobine-film-polymer-subtype",
            "Polymer subtype",
            "Sous-type de polymère",
            POLYMER_SUBTYPE_OPTIONS,
            2,
            searchable=True,
        ),
        _choice(
            "v5-bobine-film-structure",
            "Structure",
            "Structure mono ou multicouche",
            (("Monocouche", "Monocouche"), ("Multicouche", "Multicouche")),
            3,
            level=2,
        ),
        *_common_dimensions(20),
        _bool("v5-bobine-film-corona", "Corona treatment", "Traitement corona", 15, level=2),
        _text("v5-bobine-film-friction", "Friction coefficient", "Coefficient de friction", 16),
        _text(
            "v5-bobine-film-tensile-md",
            "Tensile strength MD",
            "Résistance à la traction MD",
            17,
            level=2,
        ),
        _text(
            "v5-bobine-film-tensile-cd",
            "Tensile strength CD",
            "Résistance à la traction CD",
            18,
            level=2,
        ),
        _text("v5-bobine-film-barrier-o2", "O₂ barrier", "Barrière à l'oxygène", 19, level=2),
        _text(
            "v5-bobine-film-barrier-h2o",
            "Water vapour barrier",
            "Barrière à la vapeur d'eau",
            20,
            level=2,
        ),
        _text(
            "v5-bobine-film-sealability",
            "Sealability",
            "Aptitude au scellage",
            21,
            level=2,
        ),
        _text(
            "v5-bobine-film-seal-temp",
            "Sealing temperature",
            "Température de scellage",
            22,
            level=2,
        ),
    ],
    "bobine_laminee_composite": [
        _choice(
            "v5-bobine-composite-structure",
            "Laminate structure",
            "Structure",
            COMPOSITE_STRUCTURE_OPTIONS,
            1,
            required=True,
            searchable=True,
        ),
        _text(
            "v5-bobine-composite-structure-desc",
            "Custom structure description",
            "Description de la structure",
            2,
            level=1,
        ),
        _num(
            "v5-bobine-composite-layers",
            "Layer count",
            "Nombre de couches",
            "couches",
            3,
            level=2,
            searchable=False,
            comparable=False,
        ),
        *_common_dimensions(20),
        _num(
            "v5-bobine-composite-total-grammage",
            "Total grammage",
            "Grammage total",
            "g/m²",
            15,
            level=2,
            searchable=True,
        ),
        _text("v5-bobine-composite-outer", "Outer layer", "Couche externe", 16, level=2),
        _text("v5-bobine-composite-middle", "Middle layer", "Couche intermédiaire", 17),
        _text("v5-bobine-composite-inner", "Inner layer", "Couche interne", 18, level=2),
        _text("v5-bobine-composite-barrier-o2", "O₂ barrier", "Barrière O₂", 19, level=2),
        _text("v5-bobine-composite-barrier-h2o", "H₂O barrier", "Barrière H₂O", 20, level=2),
        _bool(
            "v5-bobine-composite-light-barrier",
            "Light barrier",
            "Barrière lumière",
            21,
            level=2,
        ),
        _text(
            "v5-bobine-composite-mechanical",
            "Mechanical resistance",
            "Résistance mécanique",
            22,
            level=2,
        ),
        _text(
            "v5-bobine-composite-seal-temp",
            "Sealing temperature",
            "Température de scellage",
            23,
            level=2,
        ),
        _bool("v5-bobine-composite-print", "Printing", "Impression", 24, level=2),
    ],
    "bobine_speciale": [
        _text("v5-bobine-spec-matiere", "Material", "Matière", 1, level=1, searchable=True),
        _text("v5-bobine-spec-application", "Application", "Application", 2, level=1),
        *_common_dimensions(10),
        _text(
            "v5-bobine-spec-main-chars",
            "Main characteristics",
            "Caractéristiques principales",
            25,
            level=2,
        ),
        _text(
            "v5-bobine-spec-tech-desc",
            "Technical description",
            "Description technique",
            26,
            level=2,
        ),
        _text("v5-bobine-spec-other", "Other", "Autre", 27, level=2),
    ],
}
