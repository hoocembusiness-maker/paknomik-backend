"""Bibliothèque produits acheteur — templates illustratifs par secteur."""

from __future__ import annotations

from buyer_space.catalogue_models import SuiteEnsemble
from buyer_space.models import LibraryProduct, LibrarySector, ProductKitAssignment

# Produit bibliothèque → kit PakSuits recommandé
RECOMMENDED_SUITE_BY_CODE: dict[str, str] = {
    "creme-visage": "PKS-COS-004",
    "serum": "PKS-COS-002",
    "lotion-corps": "PKS-COS-001",
    "shampoing": "PKS-COS-001",
    "gel-douche": "PKS-COS-005",
    "masque-capillaire": "PKS-COS-004",
    "eau-de-parfum": "PKS-COS-003",
    "eau-de-toilette": "PKS-COS-003",
    "brume-parfumee": "PKS-COS-003",
    "deodorant-bille": "PKS-HYG-001",
    "deodorant-stick": "PKS-HYG-002",
    "deodorant-aerosol": "PKS-HYG-003",
    "savon-liquide": "PKS-COS-001",
    "dentifrice": "PKS-COS-005",
    "comprimes": "PKS-PHA-001",
    "sirop": "PKS-PHA-003",
    "ampoules": "PKS-PHA-005",
    "solution-ophtalmique": "PKS-PHA-006",
    "pommade": "PKS-COS-005",
    "yaourt": "PKS-PRT-002",
    "huile-alimentaire": "PKS-BEV-001",
    "epices": "PKS-FOOD-001",
    "biscuits": "PKS-FOOD-002",
    "confiture": "PKS-FOOD-002",
    "sauce": "PKS-COS-005",
    "eau-minerale": "PKS-BEV-001",
    "jus-fruit": "PKS-BEV-001",
    "boisson-gazeuse": "PKS-BEV-001",
    "huile-olive": "PKS-BEV-001",
    "patisserie": "PKS-BAK-001",
    "viennoiserie": "PKS-BAK-001",
    "gateau-individuel": "PKS-BAK-001",
    "lessive-liquide": "PKS-IND-001",
    "produit-menager": "PKS-COS-003",
    "tablettes-lave-vaisselle": "PKS-FOOD-001",
    "produit-chimique": "PKS-IND-002",
    "lubrifiant": "PKS-IND-001",
    "peinture": "PKS-IND-002",
    "produit-expedie": "PKS-ECOM-001",
    "accessoire": "PKS-ECOM-001",
    "carte-visite": "PKS-PRT-001",
    "flyer": "PKS-PRT-001",
    "brochure": "PKS-PRT-001",
    # Gemini expansion
    "dattes-conditionnees": "PKS-FOOD-003",
    "miel-artisanal": "PKS-FOOD-004",
    "couscous-vrac": "PKS-FOOD-005",
    "lait-brique": "PKS-FOOD-006",
    "complement-poudre": "PKS-PHA-009",
    "lingettes-humides": "PKS-HYG-004",
    "masque-tissu": "PKS-COS-006",
    "flacon-airless": "PKS-COS-007",
    "canette-alu": "PKS-BEV-002",
    "huile-moteur": "PKS-IND-003",
    "croquettes-petfood": "PKS-PET-001",
    "engrais-solide": "PKS-AGR-001",
    "palette-logistique": "PKS-LOG-001",
    "fromage-portion": "PKS-DAIRY-001",
}

# (sector_code, sector_label, [(product_code, product_name, brief_fr)])
LIBRARY_SEED: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    (
        "cosmetique",
        "Cosmétique",
        [
            ("creme-visage", "Crème visage", "pot de crème cosmétique générique"),
            ("serum", "Sérum", "flacon de sérum cosmétique"),
            ("lotion-corps", "Lotion corps", "flacon de lotion corporelle"),
            ("shampoing", "Shampoing", "flacon de shampoing"),
            ("gel-douche", "Gel douche", "flacon de gel douche"),
            ("masque-capillaire", "Masque capillaire", "pot de masque capillaire"),
            ("masque-tissu", "Masque tissu unitaire", "sachet monodose masque facial"),
            ("flacon-airless", "Flacon airless", "flacon pompe airless cosmétique"),
        ],
    ),
    (
        "parfumerie",
        "Parfumerie",
        [
            ("eau-de-parfum", "Eau de parfum", "flacon de parfum avec vaporisateur"),
            ("eau-de-toilette", "Eau de toilette", "flacon d'eau de toilette"),
            ("brume-parfumee", "Brume parfumée", "flacon spray de brume"),
        ],
    ),
    (
        "hygiene",
        "Hygiène",
        [
            ("deodorant-bille", "Déodorant bille", "roll-on déodorant"),
            ("deodorant-stick", "Déodorant stick", "stick déodorant twist-up"),
            ("deodorant-aerosol", "Déodorant aérosol", "canette aérosol déodorant"),
            ("savon-liquide", "Savon liquide", "flacon pompe savon"),
            ("dentifrice", "Dentifrice", "tube de dentifrice"),
            ("lingettes-humides", "Lingettes humides", "sachet de lingettes humides"),
        ],
    ),
    (
        "pharmacie",
        "Pharmacie",
        [
            ("comprimes", "Comprimés", "plaquettes de comprimés génériques"),
            ("sirop", "Sirop", "flacon de sirop pharmaceutique"),
            ("ampoules", "Ampoules", "ampoules pharmaceutiques en plateau"),
            ("solution-ophtalmique", "Solution ophtalmique", "flacon compte-gouttes pharma"),
            ("pommade", "Pommade", "tube de pommade"),
            ("complement-poudre", "Complément en poudre", "pot de complément alimentaire poudre"),
        ],
    ),
    (
        "agroalimentaire",
        "Agroalimentaire",
        [
            ("yaourt", "Yaourt", "yaourt visqueux type Yago en bouteille"),
            ("huile-alimentaire", "Huile alimentaire", "bouteille d'huile"),
            ("epices", "Épices", "sachet doypack d'épices"),
            ("biscuits", "Biscuits", "barquette / étui de biscuits"),
            ("confiture", "Confiture", "pot de confiture"),
            ("sauce", "Sauce", "flacon ou sachet de sauce"),
            ("dattes-conditionnees", "Dattes conditionnées", "dattes en coffret ou barquette"),
            ("miel-artisanal", "Miel artisanal", "pot de miel en verre"),
            ("couscous-vrac", "Couscous vrac", "sachet de couscous / semoule"),
            ("lait-brique", "Lait brique UHT", "brique carton de lait"),
        ],
    ),
    (
        "boissons",
        "Boissons",
        [
            ("eau-minerale", "Eau minérale", "bouteille PET d'eau"),
            ("jus-fruit", "Jus de fruit", "bouteille de jus"),
            ("boisson-gazeuse", "Boisson gazeuse", "canette de boisson"),
            ("huile-olive", "Huile d'olive", "bouteille d'huile d'olive"),
            ("canette-alu", "Canette aluminium", "canette aluminium de boisson"),
        ],
    ),
    (
        "boulangerie-patisserie",
        "Boulangerie-pâtisserie",
        [
            ("patisserie", "Pâtisserie", "gâteau / tarte générique"),
            ("viennoiserie", "Viennoiserie", "viennoiseries génériques"),
            ("gateau-individuel", "Gâteau individuel", "pâtisserie individuelle"),
        ],
    ),
    (
        "detergents",
        "Détergents",
        [
            ("lessive-liquide", "Lessive liquide", "jerrican / flacon de lessive"),
            ("produit-menager", "Produit ménager", "flacon spray ménager"),
            ("tablettes-lave-vaisselle", "Tablettes lave-vaisselle", "boîte de tablettes"),
        ],
    ),
    (
        "industrie",
        "Industrie",
        [
            ("produit-chimique", "Produit chimique", "seau / jerrican industriel"),
            ("lubrifiant", "Lubrifiant", "bidon de lubrifiant"),
            ("peinture", "Peinture", "pot de peinture"),
            ("huile-moteur", "Huile moteur", "bidon d'huile moteur"),
        ],
    ),
    (
        "e-commerce",
        "E-commerce",
        [
            ("produit-expedie", "Produit expédié", "objet générique prêt à expédier"),
            ("accessoire", "Accessoire", "petit accessoire e-commerce"),
        ],
    ),
    (
        "communication-imprimee",
        "Communication imprimée",
        [
            ("carte-visite", "Carte de visite", "stack de cartes de visite"),
            ("flyer", "Flyer", "flyer A5"),
            ("brochure", "Brochure", "brochure pliée"),
            ("manchon-retractable", "Manchon rétractable", "manchon sleeving imprimé"),
        ],
    ),
    (
        "pet-food",
        "Pet Food",
        [
            ("croquettes-petfood", "Croquettes pet food", "sac de croquettes animaux"),
        ],
    ),
    (
        "agriculture",
        "Agriculture",
        [
            ("engrais-solide", "Engrais solide", "sac d'engrais ou semences"),
        ],
    ),
    (
        "logistique",
        "Logistique",
        [
            ("palette-logistique", "Film palette", "film étirable de palettisation"),
        ],
    ),
    (
        "laitier",
        "Laitier",
        [
            ("fromage-portion", "Fromage en portions", "boîte de fromage en portions"),
        ],
    ),
]

PROMPT_TEMPLATE = (
    "Product packaging catalog illustration of a generic {subject}, clean studio shot, "
    "soft diffused light, 3/4 view slightly from above, no brand logos, no readable text, "
    "no barcodes, no people, no watermark, neutral cream #F6F1EA seamless background, "
    "soft contact shadow, commercial product catalog style, high detail, square composition"
)


def prompt_for(subject: str) -> str:
    return PROMPT_TEMPLATE.format(subject=subject)


def sync_library(*, overwrite_prompts: bool = False) -> dict[str, int]:
    sectors_n = products_n = linked = 0
    suite_by_code = {
        item.code: item
        for item in SuiteEnsemble.objects.filter(
            code__in=set(RECOMMENDED_SUITE_BY_CODE.values())
        )
    }
    for order, (code, label, products) in enumerate(LIBRARY_SEED):
        sector, created = LibrarySector.objects.update_or_create(
            code=code,
            defaults={"label_fr": label, "sort_order": order},
        )
        if created:
            sectors_n += 1
        for p_order, (p_code, p_name, brief) in enumerate(products):
            prompt = prompt_for(brief)
            suite = suite_by_code.get(RECOMMENDED_SUITE_BY_CODE.get(p_code, ""))
            defaults = {
                "sector": sector,
                "name": p_name,
                "description": brief,
                "suggested_filename": f"prod-{p_code}.jpg",
                "sort_order": p_order,
                "is_active": True,
                "illustrative": True,
                "recommended_suite": suite,
            }
            existing = LibraryProduct.objects.filter(code=p_code).first()
            if existing and ProductKitAssignment.objects.filter(product=existing).exists():
                if overwrite_prompts or not existing.generation_prompt:
                    existing.generation_prompt = prompt
                    existing.save(update_fields=["generation_prompt", "updated_at"])
                if existing.recommended_suite_id:
                    linked += 1
                continue
            if existing:
                for key, value in defaults.items():
                    setattr(existing, key, value)
                if overwrite_prompts or not existing.generation_prompt:
                    existing.generation_prompt = prompt
                existing.save()
            else:
                LibraryProduct.objects.create(
                    code=p_code, generation_prompt=prompt, **defaults
                )
                products_n += 1
            if suite is not None:
                linked += 1
    return {
        "sectors": sectors_n,
        "products_created": products_n,
        "recommended_linked": linked,
    }
