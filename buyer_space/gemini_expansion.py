"""Import Gemini expansion: new PakSuits kits + library products."""

from __future__ import annotations

from django.utils.text import slugify

from buyer_space.catalogue_models import (
    SuiteComponentLine,
    SuiteEnsemble,
    SuiteTaxon,
)
from buyer_space.kit_line_rebuild import _definition_for, _level, _resolve_type
from buyer_space.models import PackagingKit
from buyer_space.product_library import sync_library

# New kits from Gemini (Tableau B)
NEW_KITS: list[dict] = [
    {
        "code": "PKS-FOOD-003",
        "name": "Barquette premium thermoscellée avec couvercle transparent et étui",
        "target": "Dattes, fruits secs, pâtisseries fines",
        "description": (
            "Barquette thermoformée avec film de scellage et étui carton imprimé "
            "pour valoriser les produits du terroir."
        ),
        "sectors": ["Agroalimentaire"],
        "components": [
            ("Primaire", "Barquette thermoformée"),
            ("Primaire", "Film de scellage"),
            ("Secondaire", "Étui carton imprimé"),
        ],
    },
    {
        "code": "PKS-FOOD-004",
        "name": "Pot en verre pour miel avec couvercle twist-off et bague de sécurité",
        "target": "Miel, pâtes à tartiner, confitures artisanales",
        "description": (
            "Pot en verre, couvercle métallique twist-off, bande de scellage "
            "inviolable et étiquette."
        ),
        "sectors": ["Agroalimentaire"],
        "components": [
            ("Primaire", "Pot en verre"),
            ("Fermeture", "Couvercle métallique twist-off"),
            ("Inviolabilité", "Bande de scellage inviolable"),
            ("Identification", "Étiquette"),
        ],
    },
    {
        "code": "PKS-FOOD-005",
        "name": "Sachet coussin grand format avec soufflets latéraux",
        "target": "Semoule, couscous, farines en vrac",
        "description": (
            "Sachet plastique multicouche grand format avec soufflets, "
            "étiquette et fermeture thermosoudée."
        ),
        "sectors": ["Agroalimentaire"],
        "components": [
            ("Primaire", "Sachet plastique multicouche"),
            ("Identification", "Étiquette adhésive"),
            ("Fermeture", "Thermosoudure / lien de fermeture"),
        ],
    },
    {
        "code": "PKS-FOOD-006",
        "name": "Brique carton aseptique avec bouchon à vis bec verseur",
        "target": "Lait UHT, jus de fruits, boissons fluides",
        "description": (
            "Brique carton multicouche aseptique avec bouchon verseur plastique "
            "pour conservation longue durée."
        ),
        "sectors": ["Agroalimentaire", "Boissons"],
        "components": [
            ("Primaire", "Brique carton multicouche aseptique"),
            ("Distribution", "Bouchon verseur plastique"),
        ],
    },
    {
        "code": "PKS-PHA-009",
        "name": "Pot plastique avec opercule d'inviolabilité, capsule et cuillère",
        "target": "Poudres pharmaceutiques, compléments alimentaires",
        "description": (
            "Pot PEHD avec opercule thermoscellé, bouchon à vis et cuillère doseuse."
        ),
        "sectors": ["Pharmacie"],
        "components": [
            ("Primaire", "Pot PEHD"),
            ("Inviolabilité", "Opercule thermoscellé par induction"),
            ("Fermeture", "Bouchon à vis"),
            ("Accessoire", "Cuillère doseuse"),
        ],
    },
    {
        "code": "PKS-HYG-004",
        "name": "Sachet souple étanche avec étiquette repositionnable pour lingettes",
        "target": "Lingettes humides, nettoyants désinfectants",
        "description": (
            "Sachet laminé souple étanche avec fenêtre adhésive repositionnable."
        ),
        "sectors": ["Hygiène"],
        "components": [
            ("Primaire", "Sachet laminé souple"),
            ("Fermeture", "Fenêtre adhésive repositionnable"),
            ("Identification", "Étiquette"),
        ],
    },
    {
        "code": "PKS-COS-006",
        "name": "Sachet berlingot souple scellé à ouverture facile",
        "target": "Masques faciaux, échantillons monodoses",
        "description": (
            "Sachet laminé étanche monodose avec encoche de déchirement facile."
        ),
        "sectors": ["Cosmétique"],
        "components": [
            ("Primaire", "Sachet laminé étanche tri-soudure"),
            ("Ouverture", "Encoche de déchirement facile"),
        ],
    },
    {
        "code": "PKS-COS-007",
        "name": "Flacon pompe airless avec capsule de protection",
        "target": "Sérums haut de gamme, crèmes fluides fragiles",
        "description": (
            "Flacon airless avec pompe doseuse sous vide et capuchon de protection."
        ),
        "sectors": ["Cosmétique"],
        "components": [
            ("Primaire", "Flacon airless"),
            ("Distribution", "Pompe doseuse sous vide"),
            ("Protection", "Capuchon"),
        ],
    },
    {
        "code": "PKS-BEV-002",
        "name": "Canette aluminium avec anneau d'ouverture et film de groupage",
        "target": "Boissons gazeuses, energy drinks, jus",
        "description": (
            "Canette aluminium Easy Open avec film de fardelage pour groupage."
        ),
        "sectors": ["Boissons"],
        "components": [
            ("Primaire", "Canette en aluminium"),
            ("Ouverture", "Couvercle à languette"),
            ("Secondaire", "Film de fardelage"),
        ],
    },
    {
        "code": "PKS-IND-003",
        "name": "Bidon plastique ergonomique avec bouchon verseur inviolable",
        "target": "Huile moteur, produits chimiques d'entretien",
        "description": (
            "Bidon PEHD à poignée avec bouchon de sécurité et joint d'étanchéité."
        ),
        "sectors": ["Industrie"],
        "components": [
            ("Primaire", "Bidon PEHD avec poignée"),
            ("Fermeture", "Bouchon de sécurité"),
            ("Étanchéité", "Joint d'étanchéité"),
        ],
    },
    {
        "code": "PKS-PET-001",
        "name": "Sac grand format à soufflets avec poignée et fermeture zip",
        "target": "Croquettes et aliments pour animaux (pet food)",
        "description": (
            "Sac tissé laminé grand format avec zip refermable et poignée renforcée."
        ),
        "sectors": ["Pet Food"],
        "components": [
            ("Primaire", "Sac tissé laminé"),
            ("Fermeture", "Zip refermable"),
            ("Manutention", "Poignée latérale renforcée"),
        ],
    },
    {
        "code": "PKS-AGR-001",
        "name": "Sac robuste multicouche thermosoudé pour intrants agricoles",
        "target": "Engrais, semences, poudres minérales",
        "description": (
            "Sac polypropylène tissé enduit haute résistance, thermosoudé."
        ),
        "sectors": ["Agriculture"],
        "components": [
            ("Primaire", "Sac polypropylène tissé enduit"),
            ("Fermeture", "Thermosoudure / valve de remplissage"),
        ],
    },
    {
        "code": "PKS-LOG-001",
        "name": "Film étirable de palettisation avec mandrin carton",
        "target": "Colis e-commerce, palettes industrielles",
        "description": (
            "Film PEBD étirable de banderolage avec mandrin carton."
        ),
        "sectors": ["Logistique", "E-commerce"],
        "components": [
            ("Primaire", "Film PEBD étirable"),
            ("Support", "Mandrin carton"),
        ],
    },
    {
        "code": "PKS-PRT-002",
        "name": "Bouteille PET ou PP/PEHD avec bouchon PEHD pour yaourt visqueux",
        "target": "Yaourt à boire / yaourt visqueux type Yago (marché algérien)",
        "description": (
            "Ensemble alimentaire pour yaourt visqueux : bouteille plastique "
            "grade alimentaire (PET ou PP/PEHD), bouchon PEHD à vis, et manchon "
            "ou étiquette imprimée pour l'habillage."
        ),
        "sectors": ["Agroalimentaire", "Laitier"],
        "components": [
            ("Contenant", "Bouteille PET ou PP/PEHD"),
            ("Fermeture", "Bouchon PEHD"),
            ("Identification", "Étiquette thermorétractable / manchon"),
        ],
    },
    {
        "code": "PKS-DAIRY-001",
        "name": "Boîte circulaire en carton avec languette d'ouverture",
        "target": "Fromages fondus en portions, parts triangulaires",
        "description": (
            "Boîte carton ronde avec languette d'arrachage pour portions fromagères."
        ),
        "sectors": ["Laitier", "Agroalimentaire"],
        "components": [
            ("Primaire", "Boîte carton circulaire"),
            ("Ouverture", "Languette d'arrachage"),
            ("Fermeture", "Étiquette de fermeture"),
        ],
    },
]

def _sector_taxon(label: str) -> SuiteTaxon:
    code = f"S-{slugify(label)}"[:80]
    taxon, _ = SuiteTaxon.objects.get_or_create(
        kind=SuiteTaxon.Kind.SECTOR,
        code=code,
        defaults={"label_fr": label},
    )
    if taxon.label_fr != label:
        taxon.label_fr = label
        taxon.save(update_fields=["label_fr"])
    return taxon


def sync_new_kits() -> dict[str, int]:
    created = updated = lines_n = 0
    for kit in NEW_KITS:
        slug = slugify(kit["code"].lower())
        defaults = {
            "name": kit["name"][:240],
            "slug": slug[:80],
            "description": kit["description"],
            "target_product": kit["target"][:300],
            "function_label": "Conditionnement",
            "confidence": SuiteEnsemble.Confidence.INFERRED,
            "sourced_facts": "Proposition Gemini — à valider éditorialement.",
            "algeria_relevance": "Pertinent pour le marché algérien / maghrébin.",
            "points_to_confirm": "Composants et fournisseurs locaux à confirmer.",
            "editorial_status": SuiteEnsemble.Status.PUBLISHED,
            "priority": "P3",
        }
        ensemble = SuiteEnsemble.objects.filter(code=kit["code"]).first()
        if ensemble is None:
            ensemble = SuiteEnsemble.objects.create(code=kit["code"], **defaults)
            created += 1
        else:
            for key, value in defaults.items():
                setattr(ensemble, key, value)
            ensemble.save()
            updated += 1

        sector_ids = [_sector_taxon(label).id for label in kit["sectors"]]
        ensemble.sectors.set(sector_ids)

        # Replace lines for idempotent sync of Gemini kits only
        ensemble.lines.all().delete()
        component_labels: list[str] = []
        for index, (role, label) in enumerate(kit["components"]):
            taxon, resolved_role, _ = _resolve_type(label)
            definition = _definition_for(taxon)
            SuiteComponentLine.objects.create(
                ensemble=ensemble,
                definition=definition,
                taxon=taxon,
                role=(role or resolved_role)[:120],
                level=_level(role or resolved_role, index),
                status=SuiteComponentLine.Status.REQUIRED,
                material=_material_hint(label),
                technical_notes=label if label != taxon.label_fr else "",
                sort_order=index,
            )
            component_labels.append(label)
            lines_n += 1

        PackagingKit.objects.update_or_create(
            ensemble=ensemble,
            defaults={
                "status_label": "Publié",
                "components_label": " | ".join(component_labels),
                "component_count": len(component_labels),
                "remarks": kit.get("target", ""),
            },
        )
    return {"kits_created": created, "kits_updated": updated, "lines": lines_n}


def _material_hint(label: str) -> str:
    folded = label.casefold()
    if "pehd" in folded and "bouchon" in folded:
        return "PEHD grade alimentaire"
    if "pet" in folded or "pp/pehd" in folded or "pp / pehd" in folded:
        return "PET ou PP/PEHD grade alimentaire"
    return ""


def sync_gemini_expansion() -> dict[str, int]:
    kit_report = sync_new_kits()
    lib_report = sync_library()
    return {**kit_report, **lib_report}
