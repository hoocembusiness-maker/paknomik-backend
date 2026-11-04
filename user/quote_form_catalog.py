"""Platform quote-form catalogs, keyed by taxonomy slug.

A family entry applies to every product type in that family until a type slug
is added to TYPE_FORMS. New packaging types do not require new request code.
"""

from __future__ import annotations


def _num(key, label, unit, *, required=False, locked=False, group="dimensions", condition=None):
    return {
        "key": key,
        "label_fr": label,
        "label_en": label,
        "field_type": "number",
        "unit_label": unit,
        "required": required,
        "locked": locked,
        "positive": True,
        "group": group,
        "condition": condition,
        "options": [],
        "help_fr": "",
    }


def _choice(
    key, label, options, *, required=False, locked=False, group="technique", condition=None
):
    return {
        "key": key,
        "label_fr": label,
        "label_en": label,
        "field_type": "choice",
        "unit_label": "",
        "required": required,
        "locked": locked,
        "positive": False,
        "group": group,
        "condition": condition,
        "options": list(options),
        "help_fr": "",
    }


def _text(
    key, label, *, field_type="textarea", required=False, group="commentaire", condition=None
):
    return {
        "key": key,
        "label_fr": label,
        "label_en": label,
        "field_type": field_type,
        "unit_label": "",
        "required": required,
        "locked": False,
        "positive": False,
        "group": group,
        "condition": condition,
        "options": [],
        "help_fr": "",
    }


_PRINT = ["Aucune", "1 couleur", "Quadrichromie", "Pantone"]
_WHEN_PRINTED = {"field": "impression", "not_equals": "Aucune"}


def _print_block():
    return [
        _choice("impression", "Impression", _PRINT, required=True, group="impression"),
        _num(
            "couleurs_impression",
            "Nombre de couleurs",
            "couleurs",
            group="impression",
            condition=_WHEN_PRINTED,
        ),
        _choice(
            "finition",
            "Finition",
            ["Sans", "Vernis", "Pelliculage mat", "Pelliculage brillant"],
            group="impression",
            condition=_WHEN_PRINTED,
        ),
    ]


def generic_spec(name_fr):
    return {
        "title_fr": f"Devis — {name_fr}",
        "title_en": f"Quote — {name_fr}",
        "quantity_unit": "pièces",
        "instructions": "",
        "fields": [
            _num("longueur", "Longueur", "mm"),
            _num("largeur", "Largeur", "mm"),
            _text("matiere", "Matière", field_type="text", group="matiere"),
            _text("commentaire", "Commentaire technique"),
        ],
    }


FAMILY_FORMS = {
    "boites-et-cartons": {
        "title_fr": "Devis carton et boîte",
        "title_en": "Carton and box quote",
        "quantity_unit": "pièces",
        "instructions": "Indiquez les cotes intérieures utiles.",
        "fields": [
            _num("longueur", "Longueur", "mm", required=True, locked=True),
            _num("largeur", "Largeur", "mm", required=True, locked=True),
            _num("hauteur", "Hauteur", "mm", required=True, locked=True),
            _choice(
                "type_carton",
                "Type de carton",
                ["Carton compact", "Carton ondulé", "Carton rigide"],
                required=True,
                locked=True,
                group="matiere",
            ),
            _choice(
                "cannelure",
                "Cannelure",
                ["E", "B", "C", "BC", "BE"],
                group="matiere",
                condition={"field": "type_carton", "equals": "Carton ondulé"},
            ),
            _num("grammage", "Grammage ou résistance", "g/m²", group="matiere"),
            _choice(
                "structure",
                "Structure",
                ["Boîte pliante", "Caisse américaine", "Plateau", "Coffret"],
                group="structure",
            ),
            _choice(
                "fermeture",
                "Fermeture",
                ["Rabat rentrant", "Fond automatique", "Ruban adhésif", "Agrafes"],
                required=True,
                group="fermeture",
            ),
            *_print_block(),
            _text("commentaire", "Commentaire technique"),
        ],
    },
    "sacs-et-pochettes": {
        "title_fr": "Devis sac et sachet",
        "title_en": "Bag and pouch quote",
        "quantity_unit": "pièces",
        "instructions": "",
        "fields": [
            _choice(
                "type_sac",
                "Type",
                ["Plat", "À soufflets", "Doypack", "Avec poignées"],
                required=True,
                locked=True,
                group="structure",
            ),
            _choice(
                "matiere",
                "Matière",
                ["Kraft", "PE", "PP", "Complexe", "Non-tissé"],
                required=True,
                locked=True,
                group="matiere",
            ),
            _num("longueur", "Longueur", "mm", required=True, locked=True),
            _num("largeur", "Largeur", "mm", required=True, locked=True),
            _num(
                "soufflets",
                "Soufflets",
                "mm",
                group="dimensions",
                condition={"field": "type_sac", "in": ["À soufflets", "Doypack"]},
            ),
            _choice(
                "poignees",
                "Poignées",
                ["Sans", "Plates", "Torsadées", "Découpe"],
                group="structure",
                condition={"field": "type_sac", "equals": "Avec poignées"},
            ),
            _num("epaisseur", "Épaisseur", "µm", group="matiere"),
            *_print_block(),
            _choice(
                "fermeture",
                "Fermeture",
                ["Ouvert", "Zip", "Thermosoudé", "Lien"],
                required=True,
                group="fermeture",
            ),
            _text("commentaire", "Commentaire technique"),
        ],
    },
    "bouteilles-et-flacons": {
        "title_fr": "Devis bouteille et flacon",
        "title_en": "Bottle and vial quote",
        "quantity_unit": "pièces",
        "instructions": "Précisez le produit qui sera conditionné.",
        "fields": [
            _num("contenance", "Contenance", "ml", required=True, locked=True, group="dimensions"),
            _choice(
                "materiau",
                "Matériau",
                ["PET", "PEHD", "PP", "Verre", "Aluminium"],
                required=True,
                locked=True,
                group="matiere",
            ),
            _choice(
                "forme",
                "Forme",
                ["Ronde", "Carrée", "Ovale", "Plate"],
                required=True,
                group="structure",
            ),
            _choice(
                "couleur",
                "Couleur",
                ["Transparent", "Blanc", "Ambré", "Autre"],
                required=True,
                group="finition",
            ),
            _choice(
                "bouchon",
                "Type de bouchon",
                ["Vis", "Flip-top", "Pompe", "Spray", "Compte-gouttes", "Sans"],
                required=True,
                group="fermeture",
            ),
            _choice(
                "fermeture",
                "Fermeture",
                ["Standard", "Invilolable", "Sécurité enfant"],
                group="fermeture",
            ),
            _text(
                "compatibilite",
                "Produit conditionné",
                required=True,
                group="usage",
            ),
            _text("commentaire", "Commentaire technique"),
        ],
    },
    "etiquettes": {
        "title_fr": "Devis étiquette",
        "title_en": "Label quote",
        "quantity_unit": "pièces",
        "instructions": "",
        "fields": [
            _num("longueur", "Longueur", "mm", required=True, locked=True),
            _num("largeur", "Largeur", "mm", required=True, locked=True),
            _choice(
                "forme",
                "Forme",
                ["Rectangle", "Ronde", "Ovale", "Découpe spéciale"],
                required=True,
                group="structure",
            ),
            _choice(
                "support",
                "Support",
                ["Papier", "PP", "PE", "Papier thermique"],
                required=True,
                locked=True,
                group="matiere",
            ),
            _choice(
                "adhesif",
                "Adhésif",
                ["Permanent", "Repositionnable", "Renforcé", "Sans"],
                required=True,
                group="matiere",
            ),
            *_print_block(),
            _choice(
                "conditionnement",
                "Conditionnement",
                ["Rouleaux", "Feuilles"],
                required=True,
                locked=True,
                group="conditionnement",
            ),
            _num(
                "diametre_mandrin",
                "Diamètre du mandrin",
                "mm",
                group="conditionnement",
                condition={"field": "conditionnement", "equals": "Rouleaux"},
            ),
            _text("commentaire", "Commentaire technique"),
        ],
    },
}

# Optional per-type catalogs. A slug here replaces the family form for that type.
TYPE_FORMS: dict[str, dict] = {}
