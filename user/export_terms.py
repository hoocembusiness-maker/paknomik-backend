"""Reference data for export requests and proposals (countries, Incoterms 2020).

Only describes the vocabulary: no tax, customs or currency conversion rule is
applied. Amounts and conditions are always the ones entered by the parties.
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError

HOME_COUNTRY = "DZ"

TRADE_LOCAL = "local"
TRADE_EXPORT = "export"
TRADE_SCOPES = (TRADE_LOCAL, TRADE_EXPORT)

#: ISO 3166-1 alpha-2 codes. Names are localised by the client.
COUNTRY_CODES = tuple(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
    BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
    CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
    GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM
    IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK
    LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
    MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
    PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS
    ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
    UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    """.split()
)
EXPORT_COUNTRY_CODES = tuple(code for code in COUNTRY_CODES if code != HOME_COUNTRY)

PLACE_DEPARTURE = "departure"
PLACE_DESTINATION = "destination"
PLACE_DELIVERY = "delivery"

PLACE_LABELS = {
    PLACE_DEPARTURE: "Lieu ou port de départ",
    PLACE_DESTINATION: "Port ou lieu de destination",
    PLACE_DELIVERY: "Lieu de livraison convenu",
}

#: Incoterms 2020 — every rule is followed by a named place.
INCOTERMS = {
    "EXW": ("Ex Works — à l'usine", PLACE_DEPARTURE),
    "FCA": ("Free Carrier — franco transporteur", PLACE_DEPARTURE),
    "FAS": ("Free Alongside Ship — franco le long du navire", PLACE_DEPARTURE),
    "FOB": ("Free On Board — franco à bord", PLACE_DEPARTURE),
    "CFR": ("Cost and Freight — coût et fret", PLACE_DESTINATION),
    "CIF": ("Cost, Insurance and Freight — coût, assurance et fret", PLACE_DESTINATION),
    "CPT": ("Carriage Paid To — port payé jusqu'à", PLACE_DESTINATION),
    "CIP": ("Carriage and Insurance Paid To — port payé, assurance comprise", PLACE_DESTINATION),
    "DAP": ("Delivered At Place — rendu au lieu de destination", PLACE_DELIVERY),
    "DPU": ("Delivered at Place Unloaded — rendu déchargé", PLACE_DELIVERY),
    "DDP": ("Delivered Duty Paid — rendu droits acquittés", PLACE_DELIVERY),
}
INCOTERM_TO_DEFINE = "TBD"
INCOTERM_TO_DEFINE_LABEL = "À définir avec le fournisseur"

TRANSPORT_MODES = {
    "sea": "Maritime",
    "air": "Aérien",
    "road": "Routier",
    "rail": "Ferroviaire",
    "multimodal": "Multimodal",
}


def clean_trade_scope(value) -> str:
    scope = str(value or TRADE_LOCAL).strip().lower()
    if scope not in TRADE_SCOPES:
        raise ValidationError(
            {"error": "Type de demande invalide (local ou export).", "code": "invalid_trade_scope"}
        )
    return scope


def clean_export_country(value, *, required: bool) -> str:
    code = str(value or "").strip().upper()
    if not code:
        if required:
            raise ValidationError(
                {
                    "error": "Indiquez le pays de destination.",
                    "code": "destination_country_required",
                }
            )
        return ""
    if code == HOME_COUNTRY:
        raise ValidationError(
            {
                "error": "Pour une livraison en Algérie, utilisez le formulaire de commande local.",
                "code": "destination_is_home_country",
            }
        )
    if code not in EXPORT_COUNTRY_CODES:
        raise ValidationError(
            {"error": "Pays de destination invalide.", "code": "invalid_destination_country"}
        )
    return code


def clean_incoterm(value) -> str:
    """'' means « à définir avec le fournisseur »."""
    code = str(value or "").strip().upper()
    if code in ("", INCOTERM_TO_DEFINE):
        return ""
    if code not in INCOTERMS:
        raise ValidationError({"error": "Incoterm inconnu.", "code": "invalid_incoterm"})
    return code


def incoterm_place_label(code: str) -> str:
    rule = INCOTERMS.get(code)
    return PLACE_LABELS[rule[1]] if rule else ""


def export_options_payload() -> dict:
    return {
        "homeCountry": HOME_COUNTRY,
        "countries": list(EXPORT_COUNTRY_CODES),
        "incoterms": [
            {
                "code": code,
                "label": label,
                "placeKind": place_kind,
                "placeLabel": PLACE_LABELS[place_kind],
            }
            for code, (label, place_kind) in INCOTERMS.items()
        ],
        "incotermToDefine": {"code": INCOTERM_TO_DEFINE, "label": INCOTERM_TO_DEFINE_LABEL},
        "transportModes": [
            {"code": code, "label": label} for code, label in TRANSPORT_MODES.items()
        ],
    }
