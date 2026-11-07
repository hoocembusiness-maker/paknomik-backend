"""Paknomik taxonomy v4 — Pacdora-inspired Famille → Type (structure/forme).

Inspired by Pacdora's packaging-structure navigation (boxes, bags, bottles,
tubes, cans, labels…) adapted to a B2B marketplace. Materials and sectors
stay as attributes / ``Product.target_markets``, not as competing families.

Idempotent. Safe to re-run. Does not delete Category rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils.text import slugify

TAXONOMY_VERSION = 4
LEGACY_FAMILY_SLUG = "a-reviser"
LEGACY_TYPE_SLUG = "classification-a-confirmer"

# Historical subcategory ids (pre-v3) still accepted when present.
LEGACY_SUBCATEGORY_IDS: dict[int, str] = {
    339: "sac-en-papier",
    340: "sac-plastique",
    341: "sac-tissu-non-tisse",
    350: "sachet-doypack",
    351: "sachet-barriere",
    352: "sachet-plat",
    391: "big-bag",
    342: "etiquette-vierge",
    343: "etiquette-auto-adhesive",
    344: "etiquette-thermoretractable",
    348: "etiquette-imprimee",
    353: "planche-en-carton",
    354: "boite-pliante",
    355: "boite-presentoir-display",
    356: "pochette-papier-carton",
    357: "coffret",
    358: "boite-rigide",
    418: "tube-en-carton",
    360: "caisse-carton",
    361: "caisse-fruits-legumes",
    362: "papier-kraft-rouleau",
    397: "papier-cadeau",
    398: "papier-de-soie",
    363: "bouteille-pet",
    364: "flacon-plastique",
    365: "bouteille-verre",
    366: "canette-aluminium",
    367: "flacon-compte-gouttes",
    368: "flacon-pulverisateur",
    369: "pot-verre",
    370: "pot-plastique",
    371: "flacon-pharmaceutique",
    372: "ampoule-emballage",
    373: "bouchon-vis-plastique",
    375: "bouchon-flip-top",
    376: "fermeture-compte-gouttes",
    409: "bouchon-aluminium",
    384: "tube-souple-lamine",
    385: "tube-aluminium",
    386: "tube-plastique",
    387: "tube-mascara",
    395: "film-retractable",
    396: "film-a-bulles",
    399: "film-alimentaire",
    419: "film-etirable",
    420: "film-fardelage",
    431: "bobine-papier",
    432: "bobine-aluminium",
    433: "bobine-pvc",
    434: "bobine-film-plastique",
    435: "bobine-laminee-composite",
    436: "bobine-speciale",
    388: "bidon-plastique",
    389: "jerricane",
    390: "fut-industriel",
    334: "mousse-protection",
    417: "impression-personnalisee",
    # v3 transitional slugs as ids may still point at renamed rows
    421: "contenant-alimentaire",
    422: "boite-pliante",
    423: "boite-pliante",
    424: "planche-en-carton",
}

# Former v3 type slug → v4 type slug
V3_TYPE_TO_V4: dict[str, str] = {
    "boite-pliable": "boite-pliante",
    "boite-display-presentoir": "boite-presentoir-display",
    "caisse-en-carton": "caisse-carton",
    "pochette-doypack": "sachet-doypack",
    "sachet-industriel-big-bag": "big-bag",
    "boite-alimentaire-take-away": "contenant-alimentaire",
    "boite-a-pizza": "boite-pliante",
    "boite-pour-tacos": "boite-pliante",
    "palette-maquillage": "boite-rigide",
    "autres-papier-carton": "planche-en-carton",
    "papier-aluminium-rouleau": "papier-aluminium-rouleau",
    "impression-sur-emballage": "impression-sur-emballage-souple",
    "impression-sur-film": "impression-sur-emballage-souple",
}

# Explicit product overrides (structure-first classification).
PRODUCT_TYPE_OVERRIDES: dict[int, str] = {
    1: "gobelet",
    3: "barquette",
    23: "contenant-alimentaire",
    24: "contenant-alimentaire",
    25: "contenant-alimentaire",
    26: "contenant-alimentaire",
    28: "barquette",
    30: "boite-pliante",
    31: "boite-pliante",
    37: "boite-pliante",
    2: "boite-cadeau",
    29: "boite-pliante",
    35: "barquette",
    # Ambiguous commercial / POS items — keep in review, do not guess structure.
    20: LEGACY_TYPE_SLUG,
    36: LEGACY_TYPE_SLUG,
    33: "sachet-plat",
}

PRODUCT_SECTOR_EXTRAS: dict[int, list[str]] = {
    1: ["restaurant"],
    3: ["restaurant"],
    23: ["restaurant", "cafe"],
    24: ["restaurant", "cafe"],
    25: ["restaurant", "cafe"],
    26: ["restaurant", "cafe"],
    28: ["restaurant"],
    30: ["restaurant", "cafe", "restauration-rapide"],
    31: ["restaurant", "cafe", "restauration-rapide"],
    37: ["restaurant"],
    29: ["ecommerce"],
    35: ["commerce", "industrie"],
}

PHARMA_FINISHED_SUBCATEGORY_IDS = {400, 401, 402, 403, 404, 405, 406, 407, 408}


@dataclass
class FamilySpec:
    slug: str
    name_fr: str
    name_en: str
    sort_order: int
    is_service: bool = False
    reuse_slugs: list[str] = field(default_factory=list)


@dataclass
class TypeSpec:
    family_slug: str
    slug: str
    name_fr: str
    name_en: str
    sort_order: int
    is_service: bool = False
    reuse_slugs: list[str] = field(default_factory=list)


FAMILIES: list[FamilySpec] = [
    FamilySpec(
        "boites-et-cartons",
        "Boîtes et cartons",
        "Boxes and cartons",
        10,
        reuse_slugs=["cartonnage-emballages-papier", "carton-derivatives"],
    ),
    FamilySpec(
        "sacs-et-pochettes",
        "Sacs et pochettes",
        "Bags and pouches",
        20,
        reuse_slugs=["sacs-et-sachets", "sacs"],
    ),
    FamilySpec(
        "bouteilles-et-flacons",
        "Bouteilles et flacons",
        "Bottles and vials",
        30,
        reuse_slugs=["contenants-rigides", "flacons-bouteilles"],
    ),
    FamilySpec(
        "pots-gobelets-contenants",
        "Pots, gobelets et contenants",
        "Jars, cups and containers",
        40,
    ),
    FamilySpec(
        "canettes-contenants-metalliques",
        "Canettes et contenants métalliques",
        "Cans and metal containers",
        50,
    ),
    FamilySpec(
        "tubes",
        "Tubes",
        "Tubes",
        60,
        reuse_slugs=["tubes-emballages-tubulaires"],
    ),
    FamilySpec("etiquettes", "Étiquettes", "Labels", 70, reuse_slugs=["etiquettes"]),
    FamilySpec(
        "fermetures-accessoires",
        "Fermetures et accessoires",
        "Closures and accessories",
        80,
        reuse_slugs=["fermetures", "bouchons"],
    ),
    FamilySpec(
        "films-materiaux-souples",
        "Films et matériaux souples",
        "Films and flexible materials",
        90,
        reuse_slugs=["films-materiaux-rouleaux", "films"],
    ),
    FamilySpec(
        "papiers-emballage",
        "Papiers d'emballage",
        "Packaging papers",
        100,
    ),
    FamilySpec(
        "emballage-industriel-protection",
        "Emballage industriel et protection",
        "Industrial packaging and protection",
        110,
        reuse_slugs=["emballage-industriel-protection", "emballage-industriel"],
    ),
    FamilySpec(
        "services-impression",
        "Services d'impression",
        "Printing services",
        120,
        is_service=True,
        reuse_slugs=["services-impression", "impression-a-la-demande"],
    ),
    FamilySpec(
        LEGACY_FAMILY_SLUG,
        "À réviser",
        "Needs review",
        900,
        reuse_slugs=["a-reviser"],
    ),
]

TYPES: list[TypeSpec] = [
    # Boîtes et cartons
    TypeSpec("boites-et-cartons", "boite-pliante", "Boîte pliante", "Folding carton", 10, reuse_slugs=["boite-pliable", "boite-pliante"]),
    TypeSpec("boites-et-cartons", "boite-a-rabat", "Boîte à rabat", "Tuck-end box", 20),
    TypeSpec("boites-et-cartons", "boite-rigide", "Boîte rigide", "Rigid setup box", 30, reuse_slugs=["palette-maquillage", "boite-rigide"]),
    TypeSpec("boites-et-cartons", "boite-cadeau", "Boîte cadeau", "Gift box", 40),
    TypeSpec("boites-et-cartons", "boite-expedition", "Boîte d'expédition", "Shipping box", 50),
    TypeSpec("boites-et-cartons", "boite-plateau", "Boîte plateau", "Tray box", 60),
    TypeSpec(
        "boites-et-cartons",
        "boite-presentoir-display",
        "Boîte présentoir / display",
        "Display box",
        70,
        reuse_slugs=["boite-display-presentoir", "boite-presentoir-display"],
    ),
    TypeSpec("boites-et-cartons", "caisse-carton", "Caisse carton", "Corrugated case", 80, reuse_slugs=["caisse-en-carton", "caisse-carton"]),
    TypeSpec(
        "boites-et-cartons",
        "caisse-fruits-legumes",
        "Caisse carton pour fruits et légumes",
        "Fruit and vegetable carton",
        90,
        reuse_slugs=["caisse-fruits-legumes"],
    ),
    TypeSpec("boites-et-cartons", "coffret", "Coffret", "Coffret / luxury box", 100, reuse_slugs=["coffret"]),
    TypeSpec(
        "boites-et-cartons",
        "pochette-papier-carton",
        "Pochette en papier ou carton",
        "Paper or cardboard pouch",
        110,
        reuse_slugs=["pochette-papier-carton"],
    ),
    TypeSpec(
        "boites-et-cartons",
        "planche-en-carton",
        "Planche en carton",
        "Chipboard",
        120,
        reuse_slugs=["planche-en-carton", "autres-papier-carton"],
    ),
    # Sacs et pochettes
    TypeSpec("sacs-et-pochettes", "sac-en-papier", "Sac en papier", "Paper bag", 10, reuse_slugs=["sac-en-papier"]),
    TypeSpec("sacs-et-pochettes", "sac-plastique", "Sac plastique", "Plastic bag", 20, reuse_slugs=["sac-plastique"]),
    TypeSpec(
        "sacs-et-pochettes",
        "sac-tissu-non-tisse",
        "Sac en tissu non tissé",
        "Non-woven cloth bag",
        30,
        reuse_slugs=["sac-tissu-non-tisse"],
    ),
    TypeSpec("sacs-et-pochettes", "sachet-plat", "Sachet plat", "Flat pouch", 40, reuse_slugs=["sachet-plat"]),
    TypeSpec("sacs-et-pochettes", "sachet-barriere", "Sachet barrière", "Barrier pouch", 50, reuse_slugs=["sachet-barriere"]),
    TypeSpec(
        "sacs-et-pochettes",
        "sachet-doypack",
        "Sachet doypack",
        "Stand-up pouch / doypack",
        60,
        reuse_slugs=["pochette-doypack", "sachet-doypack"],
    ),
    TypeSpec("sacs-et-pochettes", "pochette-souple", "Pochette souple", "Flexible pouch", 70),
    TypeSpec("sacs-et-pochettes", "big-bag", "Big bag", "Big bag / FIBC", 80, reuse_slugs=["sachet-industriel-big-bag", "big-bag"]),
    # Bouteilles et flacons
    TypeSpec("bouteilles-et-flacons", "bouteille-pet", "Bouteille PET", "PET bottle", 10, reuse_slugs=["bouteille-pet"]),
    TypeSpec("bouteilles-et-flacons", "bouteille-plastique", "Bouteille en plastique", "Plastic bottle", 20),
    TypeSpec("bouteilles-et-flacons", "bouteille-verre", "Bouteille en verre", "Glass bottle", 30, reuse_slugs=["bouteille-verre"]),
    TypeSpec("bouteilles-et-flacons", "flacon-plastique", "Flacon en plastique", "Plastic vial", 40, reuse_slugs=["flacon-plastique"]),
    TypeSpec(
        "bouteilles-et-flacons",
        "flacon-pharmaceutique",
        "Flacon pharmaceutique",
        "Pharmaceutical bottle",
        50,
        reuse_slugs=["flacon-pharmaceutique"],
    ),
    TypeSpec(
        "bouteilles-et-flacons",
        "flacon-compte-gouttes",
        "Flacon compte-gouttes",
        "Dropper bottle",
        60,
        reuse_slugs=["flacon-compte-gouttes"],
    ),
    TypeSpec(
        "bouteilles-et-flacons",
        "flacon-pulverisateur",
        "Flacon pulvérisateur",
        "Spray bottle",
        70,
        reuse_slugs=["flacon-pulverisateur"],
    ),
    TypeSpec(
        "bouteilles-et-flacons",
        "ampoule-emballage",
        "Ampoule d'emballage",
        "Packaging ampoule",
        80,
        reuse_slugs=["ampoule-emballage"],
    ),
    # Pots, gobelets et contenants
    TypeSpec("pots-gobelets-contenants", "pot-plastique", "Pot en plastique", "Plastic jar", 10, reuse_slugs=["pot-plastique"]),
    TypeSpec("pots-gobelets-contenants", "pot-verre", "Pot en verre", "Glass jar", 20, reuse_slugs=["pot-verre"]),
    TypeSpec("pots-gobelets-contenants", "gobelet", "Gobelet", "Cup", 30),
    TypeSpec("pots-gobelets-contenants", "barquette", "Barquette", "Tray", 40),
    TypeSpec(
        "pots-gobelets-contenants",
        "contenant-alimentaire",
        "Contenant alimentaire",
        "Food container",
        50,
        reuse_slugs=["boite-alimentaire-take-away", "contenant-alimentaire"],
    ),
    # Canettes
    TypeSpec(
        "canettes-contenants-metalliques",
        "canette-aluminium",
        "Canette en aluminium",
        "Aluminum can",
        10,
        reuse_slugs=["canette-aluminium"],
    ),
    TypeSpec("canettes-contenants-metalliques", "boite-metallique", "Boîte métallique", "Metal tin", 20),
    TypeSpec(
        "canettes-contenants-metalliques",
        "contenant-metallique",
        "Contenant métallique",
        "Metal container",
        30,
    ),
    # Tubes
    TypeSpec("tubes", "tube-en-carton", "Tube en carton", "Cardboard tube", 10, reuse_slugs=["tube-en-carton"]),
    TypeSpec("tubes", "mandrin-en-carton", "Mandrin en carton", "Cardboard core", 20, reuse_slugs=["mandrin-en-carton"]),
    TypeSpec("tubes", "tube-souple-lamine", "Tube souple laminé", "Laminated soft tube", 30, reuse_slugs=["tube-souple-lamine"]),
    TypeSpec("tubes", "tube-aluminium", "Tube en aluminium", "Aluminum tube", 40, reuse_slugs=["tube-aluminium"]),
    TypeSpec("tubes", "tube-plastique", "Tube en plastique", "Plastic tube", 50, reuse_slugs=["tube-plastique"]),
    TypeSpec("tubes", "tube-mascara", "Tube mascara", "Mascara tube", 60, reuse_slugs=["tube-mascara"]),
    # Étiquettes
    TypeSpec("etiquettes", "etiquette-vierge", "Étiquette vierge", "Blank label", 10, reuse_slugs=["etiquette-vierge"]),
    TypeSpec(
        "etiquettes",
        "etiquette-auto-adhesive",
        "Étiquette auto-adhésive",
        "Self-adhesive label",
        20,
        reuse_slugs=["etiquette-auto-adhesive"],
    ),
    TypeSpec("etiquettes", "etiquette-imprimee", "Étiquette imprimée", "Printed label", 30, reuse_slugs=["etiquette-imprimee"]),
    TypeSpec(
        "etiquettes",
        "etiquette-thermoretractable",
        "Étiquette thermorétractable / manchon",
        "Shrink sleeve label",
        40,
        reuse_slugs=["etiquette-thermoretractable"],
    ),
    # Fermetures
    TypeSpec(
        "fermetures-accessoires",
        "bouchon-vis-plastique",
        "Bouchon à vis en plastique",
        "Plastic screw cap",
        10,
        reuse_slugs=["bouchon-vis-plastique"],
    ),
    TypeSpec(
        "fermetures-accessoires",
        "bouchon-flip-top",
        "Bouchon flip-top",
        "Flip-top cap",
        20,
        reuse_slugs=["bouchon-flip-top"],
    ),
    TypeSpec(
        "fermetures-accessoires",
        "fermeture-compte-gouttes",
        "Fermeture compte-gouttes",
        "Dropper closure",
        30,
        reuse_slugs=["fermeture-compte-gouttes"],
    ),
    TypeSpec(
        "fermetures-accessoires",
        "bouchon-aluminium",
        "Bouchon en aluminium",
        "Aluminum cap",
        40,
        reuse_slugs=["bouchon-aluminium"],
    ),
    # Films
    TypeSpec("films-materiaux-souples", "film-etirable", "Film étirable", "Stretch film", 10, reuse_slugs=["film-etirable"]),
    TypeSpec("films-materiaux-souples", "film-retractable", "Film rétractable", "Shrink film", 20, reuse_slugs=["film-retractable"]),
    TypeSpec("films-materiaux-souples", "film-a-bulles", "Film à bulles", "Bubble film", 30, reuse_slugs=["film-a-bulles"]),
    TypeSpec("films-materiaux-souples", "film-alimentaire", "Film alimentaire", "Food film", 40, reuse_slugs=["film-alimentaire"]),
    TypeSpec("films-materiaux-souples", "film-fardelage", "Film de fardelage", "Bundling film", 50, reuse_slugs=["film-fardelage"]),
    TypeSpec(
        "films-materiaux-souples",
        "bobine-film-plastique",
        "Bobine de film plastique",
        "Plastic film reel",
        60,
        reuse_slugs=["bobine-film-plastique"],
    ),
    TypeSpec("films-materiaux-souples", "bobine-pvc", "Bobine de PVC", "PVC reel", 70, reuse_slugs=["bobine-pvc"]),
    TypeSpec(
        "films-materiaux-souples",
        "bobine-laminee-composite",
        "Bobine laminée / composite",
        "Laminated / composite reel",
        80,
        reuse_slugs=["bobine-laminee-composite"],
    ),
    TypeSpec("films-materiaux-souples", "bobine-speciale", "Bobine spéciale", "Special reel", 90, reuse_slugs=["bobine-speciale"]),
    # Papiers
    TypeSpec(
        "papiers-emballage",
        "papier-kraft-rouleau",
        "Papier kraft en rouleau",
        "Kraft paper roll",
        10,
        reuse_slugs=["papier-kraft-rouleau"],
    ),
    TypeSpec("papiers-emballage", "papier-cadeau", "Papier cadeau", "Gift wrap", 20, reuse_slugs=["papier-cadeau"]),
    TypeSpec("papiers-emballage", "papier-de-soie", "Papier de soie", "Tissue paper", 30, reuse_slugs=["papier-de-soie"]),
    TypeSpec(
        "papiers-emballage",
        "papier-aluminium-rouleau",
        "Papier aluminium en rouleau",
        "Aluminum foil roll",
        40,
        reuse_slugs=["papier-aluminium-rouleau"],
    ),
    TypeSpec("papiers-emballage", "bobine-papier", "Bobine de papier", "Paper reel", 50, reuse_slugs=["bobine-papier"]),
    TypeSpec("papiers-emballage", "bobine-aluminium", "Bobine d'aluminium", "Aluminium reel", 60, reuse_slugs=["bobine-aluminium"]),
    # Industriel
    TypeSpec(
        "emballage-industriel-protection",
        "bidon-plastique",
        "Bidon en plastique",
        "Plastic jug",
        10,
        reuse_slugs=["bidon-plastique"],
    ),
    TypeSpec("emballage-industriel-protection", "jerricane", "Jerricane", "Jerrican", 20, reuse_slugs=["jerricane"]),
    TypeSpec("emballage-industriel-protection", "fut-industriel", "Fût industriel", "Industrial drum", 30, reuse_slugs=["fut-industriel"]),
    TypeSpec("emballage-industriel-protection", "seau-industriel", "Seau industriel", "Industrial pail", 40, reuse_slugs=["seau-industriel"]),
    TypeSpec(
        "emballage-industriel-protection",
        "mousse-protection",
        "Mousse de protection",
        "Protective foam",
        50,
        reuse_slugs=["mousse-protection"],
    ),
    # Services
    TypeSpec(
        "services-impression",
        "impression-sur-carton",
        "Impression sur carton",
        "Carton printing",
        10,
        is_service=True,
        reuse_slugs=["impression-sur-carton"],
    ),
    TypeSpec(
        "services-impression",
        "impression-sur-emballage-souple",
        "Impression sur emballage souple",
        "Flexible packaging printing",
        20,
        is_service=True,
        reuse_slugs=["impression-sur-emballage", "impression-sur-film", "impression-sur-emballage-souple"],
    ),
    TypeSpec(
        "services-impression",
        "impression-sur-etiquette",
        "Impression sur étiquette",
        "Label printing",
        30,
        is_service=True,
        reuse_slugs=["impression-sur-etiquette"],
    ),
    TypeSpec(
        "services-impression",
        "impression-personnalisee",
        "Impression personnalisée à la demande",
        "On-demand custom printing",
        40,
        is_service=True,
        reuse_slugs=["impression-personnalisee"],
    ),
    TypeSpec(
        "services-impression",
        "autres-services-impression",
        "Autres services d'impression",
        "Other printing services",
        50,
        is_service=True,
        reuse_slugs=["autres-services-impression"],
    ),
    TypeSpec(LEGACY_FAMILY_SLUG, LEGACY_TYPE_SLUG, "Classification à confirmer", "Classification to confirm", 10),
]

ACTIVE_FAMILY_SLUGS = frozenset(s.slug for s in FAMILIES if s.slug != LEGACY_FAMILY_SLUG)
ACTIVE_TYPE_SLUGS = frozenset(s.slug for s in TYPES if s.slug != LEGACY_TYPE_SLUG)
SERVICE_FAMILY_SLUGS = frozenset(s.slug for s in FAMILIES if s.is_service)
SERVICE_TYPE_SLUGS = frozenset(s.slug for s in TYPES if s.is_service)


def _find_category(*, reuse_slugs=None, slug=None):
    from user.models import Category

    for candidate in [slug, *(reuse_slugs or [])]:
        if not candidate:
            continue
        row = Category.objects.filter(slug=candidate).first()
        if row:
            return row
    return None


def _ensure_unique_slug(desired: str, exclude_id: int | None = None) -> str:
    from user.models import Category

    base = slugify(desired)[:70] or "category"
    slug = base
    index = 2
    while Category.objects.filter(slug=slug).exclude(pk=exclude_id).exists():
        slug = f"{base}-{index}"
        index += 1
    return slug


def _upsert_family(spec: FamilySpec):
    from user.models import Category

    row = _find_category(reuse_slugs=spec.reuse_slugs, slug=spec.slug)
    if row is None:
        row = Category(name_en=spec.name_en)
    row.parent = None
    row.level = 1
    row.name_en = spec.name_en
    row.name_fr = spec.name_fr
    row.sort_order = spec.sort_order
    row.slug = _ensure_unique_slug(spec.slug, exclude_id=row.pk)
    row.save()
    return row


def _upsert_type(spec: TypeSpec, family_by_slug: dict):
    from user.models import Category

    family = family_by_slug[spec.family_slug]
    row = _find_category(reuse_slugs=spec.reuse_slugs, slug=spec.slug)
    if row is None:
        row = Category(name_en=spec.name_en)
    row.parent = family
    row.level = 2
    row.name_en = spec.name_en
    row.name_fr = spec.name_fr
    row.sort_order = spec.sort_order
    row.slug = _ensure_unique_slug(spec.slug, exclude_id=row.pk)
    row.save()
    return row


def _merge_target_markets(product, extras: list[str]) -> None:
    from user.product_markets import normalize_target_markets

    current = list(product.target_markets or [])
    merged = normalize_target_markets([*current, *extras])
    if merged != current:
        product.target_markets = merged
        product.save(update_fields=["target_markets"])


def _resolve_target_slug(product) -> tuple[str | None, bool]:
    """Return (type_slug, is_ambiguous)."""
    if product.id in PRODUCT_TYPE_OVERRIDES:
        mapped = PRODUCT_TYPE_OVERRIDES[product.id]
        return mapped, mapped == LEGACY_TYPE_SLUG

    old_sub_id = product.subcategory_id
    old_slug = product.subcategory.slug if product.subcategory_id else None

    if old_sub_id in PHARMA_FINISHED_SUBCATEGORY_IDS:
        return LEGACY_TYPE_SLUG, False

    if old_slug and old_slug in ACTIVE_TYPE_SLUGS:
        return old_slug, False

    if old_slug and old_slug in V3_TYPE_TO_V4:
        mapped = V3_TYPE_TO_V4[old_slug]
        return mapped, mapped is None

    if old_sub_id and old_sub_id in LEGACY_SUBCATEGORY_IDS:
        return LEGACY_SUBCATEGORY_IDS[old_sub_id], False

    name = (product.name or "").casefold()
    if "gobelet" in name:
        return "gobelet", False
    if "barquette" in name:
        return "barquette", False
    if "fruit" in name or "légume" in name or "legume" in name:
        return "caisse-fruits-legumes", False
    if "pizza" in name or "taco" in name:
        return "boite-pliante", False
    if "industriel" in name or "pav" in name or "plv" in name:
        return LEGACY_TYPE_SLUG, True
    if old_slug == LEGACY_TYPE_SLUG or old_sub_id is None:
        return LEGACY_TYPE_SLUG, True
    return LEGACY_TYPE_SLUG, True


def _remap_products(type_by_slug: dict, legacy_type) -> dict:
    from user.crm import fields_missing_from_db
    from user.models import Product

    report = {
        "remapped": 0,
        "legacy": 0,
        "unchanged": 0,
        "ambiguous_product_ids": [],
        "pharma_finished_kept_legacy": [],
        "mapping_notes": [],
    }

    products = Product.objects.select_related("category", "subcategory").defer(
        *fields_missing_from_db(Product)
    )
    for product in products.iterator():
        target_slug, ambiguous = _resolve_target_slug(product)
        if product.subcategory_id in PHARMA_FINISHED_SUBCATEGORY_IDS:
            report["pharma_finished_kept_legacy"].append(product.id)

        target = type_by_slug.get(target_slug) if target_slug else None
        if target is None:
            target = legacy_type
            ambiguous = True

        if ambiguous and product.id not in report["ambiguous_product_ids"]:
            report["ambiguous_product_ids"].append(product.id)
            report["mapping_notes"].append(
                f"#{product.id} «{product.name}» → {target.slug} (à valider)"
            )

        family = target.parent
        changed = product.subcategory_id != target.id or product.category_id != family.id
        if changed:
            product.subcategory = target
            product.category = family
            product.save(update_fields=["subcategory", "category"])
            if target.id == legacy_type.id:
                report["legacy"] += 1
            else:
                report["remapped"] += 1
        else:
            report["unchanged"] += 1

        extras = PRODUCT_SECTOR_EXTRAS.get(product.id)
        if extras:
            _merge_target_markets(product, extras)

    return report


def _remap_supplier_interests(family_by_slug: dict, type_by_slug: dict) -> None:
    from user.models import Supplier

    active_families = [f for slug, f in family_by_slug.items() if slug in ACTIVE_FAMILY_SLUGS]
    for supplier in Supplier.objects.prefetch_related(
        "interested_categories", "interested_subcategories"
    ).iterator():
        new_types = []
        for sub in supplier.interested_subcategories.all():
            slug = sub.slug
            if slug in V3_TYPE_TO_V4:
                slug = V3_TYPE_TO_V4[slug] or slug
            if slug in type_by_slug and slug in ACTIVE_TYPE_SLUGS:
                new_types.append(type_by_slug[slug])
            elif sub.id in LEGACY_SUBCATEGORY_IDS:
                mapped = LEGACY_SUBCATEGORY_IDS[sub.id]
                if mapped in type_by_slug:
                    new_types.append(type_by_slug[mapped])
        if new_types:
            supplier.interested_subcategories.set(new_types)
            family_ids = {t.parent_id for t in new_types if t.parent_id}
            families = [f for f in active_families if f.id in family_ids]
            if families:
                supplier.interested_categories.set(families)


def _remap_campaigns(type_by_slug: dict, legacy_type) -> None:
    from user.models import Campaign

    if not hasattr(Campaign, "subcategory_id"):
        return
    for campaign in Campaign.objects.exclude(subcategory_id=None).select_related("subcategory"):
        slug = campaign.subcategory.slug if campaign.subcategory_id else None
        if slug in V3_TYPE_TO_V4:
            slug = V3_TYPE_TO_V4[slug]
        target = type_by_slug.get(slug) if slug else None
        if target is None and campaign.subcategory_id in LEGACY_SUBCATEGORY_IDS:
            target = type_by_slug.get(LEGACY_SUBCATEGORY_IDS[campaign.subcategory_id])
        if target is None:
            target = legacy_type
        if campaign.subcategory_id != target.id:
            campaign.subcategory = target
            campaign.save(update_fields=["subcategory"])


def _retire_unused_nodes(keep_ids: set[int], legacy_family) -> int:
    from user.models import Category

    retired = 0
    for row in Category.objects.exclude(pk__in=keep_ids):
        if row.pk == legacy_family.pk:
            continue
        if row.parent_id != legacy_family.id or row.level != 2:
            row.parent = legacy_family
            row.level = 2
            row.save(update_fields=["parent", "level"])
            retired += 1
    return retired


def _relocate_pharma_finished(legacy_family) -> None:
    from user.models import Category

    for pk in PHARMA_FINISHED_SUBCATEGORY_IDS:
        row = Category.objects.filter(pk=pk).first()
        if not row:
            continue
        row.parent = legacy_family
        row.level = 2
        if not row.slug:
            row.slug = _ensure_unique_slug(row.name_en or f"pharma-{pk}", exclude_id=row.pk)
        row.save()


@transaction.atomic
def apply_taxonomy_v4(*, dry_run: bool = False) -> dict:
    if dry_run:
        sid = transaction.savepoint()
        report = _apply_inner()
        transaction.savepoint_rollback(sid)
        report["dry_run"] = True
        return report
    return _apply_inner()


def _apply_inner() -> dict:
    family_by_slug = {spec.slug: _upsert_family(spec) for spec in FAMILIES}
    type_by_slug = {spec.slug: _upsert_type(spec, family_by_slug) for spec in TYPES}

    legacy_family = family_by_slug[LEGACY_FAMILY_SLUG]
    legacy_type = type_by_slug[LEGACY_TYPE_SLUG]
    _relocate_pharma_finished(legacy_family)

    product_report = _remap_products(type_by_slug, legacy_type)
    _remap_supplier_interests(family_by_slug, type_by_slug)
    _remap_campaigns(type_by_slug, legacy_type)

    keep_ids = {f.id for f in family_by_slug.values()} | {t.id for t in type_by_slug.values()}
    keep_ids |= set(PHARMA_FINISHED_SUBCATEGORY_IDS)
    retired = _retire_unused_nodes(keep_ids, legacy_family)

    return {
        "version": TAXONOMY_VERSION,
        "families": len(ACTIVE_FAMILY_SLUGS),
        "types": len(ACTIVE_TYPE_SLUGS),
        "retired_nodes": retired,
        **product_report,
        "family_slugs": sorted(ACTIVE_FAMILY_SLUGS),
        "type_slugs": sorted(ACTIVE_TYPE_SLUGS),
    }


def is_service_category(category) -> bool:
    if not category:
        return False
    if category.parent_id is None:
        return category.slug in SERVICE_FAMILY_SLUGS
    return category.slug in SERVICE_TYPE_SLUGS or (
        bool(category.parent and category.parent.slug in SERVICE_FAMILY_SLUGS)
    )


def is_nav_family(category) -> bool:
    return bool(category and category.parent_id is None and category.slug in ACTIVE_FAMILY_SLUGS)


def is_nav_type(category) -> bool:
    return bool(
        category
        and category.parent_id
        and category.slug in ACTIVE_TYPE_SLUGS
        and category.parent
        and category.parent.slug in ACTIVE_FAMILY_SLUGS
    )


def active_families_queryset():
    from user.models import Category

    return Category.objects.filter(parent__isnull=True, slug__in=ACTIVE_FAMILY_SLUGS)


def active_types_queryset(family=None):
    from user.models import Category

    qs = Category.objects.filter(
        parent__isnull=False,
        slug__in=ACTIVE_TYPE_SLUGS,
        parent__slug__in=ACTIVE_FAMILY_SLUGS,
    )
    if family is not None:
        qs = qs.filter(parent=family)
    return qs
