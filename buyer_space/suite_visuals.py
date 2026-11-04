"""Generic illustration kinds for PakSuits components.

One drawing serves every ensemble that uses the same component shape.
Kinds without a drawing stay « à produire ».
"""

from __future__ import annotations

# Drawn / generated library. Order is the match priority (first hit wins).
VISUAL_RULES: tuple[tuple[str, str], ...] = (
    ("compte-gouttes", "compte-gouttes"),
    ("compte gouttes", "compte-gouttes"),
    ("actionneur", "actionneur"),
    ("pompe", "pompe"),
    ("spray", "pompe"),
    ("pulver", "pompe"),
    ("valve", "pompe"),
    ("robinet", "pompe"),
    ("bille", "bille"),
    ("roll-on", "bille"),
    ("roll on", "bille"),
    ("stick", "stick"),
    ("twist-up", "stick"),
    ("canette", "canette"),
    ("aerosol", "canette"),
    ("aérosol", "canette"),
    ("bouchon", "bouchon"),
    ("capuchon", "bouchon"),
    ("flip-top", "bouchon"),
    ("fermeture zip", "zip"),
    ("zip", "zip"),
    ("joint", "joint"),
    ("gasket", "joint"),
    ("pince", "pince"),
    ("clip", "pince"),
    ("papier gras", "papier-gras"),
    ("mousse", "mousse"),
    ("calage", "mousse"),
    ("palette", "palette"),
    ("pallet", "palette"),
    ("carte de visite", "carte"),
    ("flyer", "flyer"),
    ("etiquette", "etiquette"),
    ("étiquette", "etiquette"),
    ("manchon", "etiquette"),
    ("opercule", "opercule"),
    ("liner", "opercule"),
    ("caisse", "caisse"),
    ("expedition", "caisse"),
    ("expédition", "caisse"),
    ("boite", "boite-pliante"),
    ("boîte", "boite-pliante"),
    ("etui", "boite-pliante"),
    ("étui", "boite-pliante"),
    ("coffret", "boite-pliante"),
    ("rabat", "boite-pliante"),
    ("seau", "seau"),
    ("jerricane", "seau"),
    ("tube", "tube"),
    ("doypack", "sachet"),
    ("sachet", "sachet"),
    ("pochette", "sachet"),
    ("flacon", "flacon"),
    ("bouteille", "flacon"),
    ("ampoule", "ampoule"),
    ("barquette", "barquette"),
    ("gobelet", "pot"),
    ("pot", "pot"),
    ("film", "film"),
    ("bobine", "film"),
    ("notice", "notice"),
    ("planche", "notice"),
    ("ruban", "ruban"),
)

# Kinds that have (or will have) a generated asset in media/paksuites/
PRODUCED_KINDS = frozenset(
    {
        "ampoule",
        "barquette",
        "boite-pliante",
        "bouchon",
        "caisse",
        "compte-gouttes",
        "etiquette",
        "film",
        "flacon",
        "generique",
        "notice",
        "opercule",
        "pompe",
        "pot",
        "sachet",
        "seau",
        "tube",
        "actionneur",
        "bille",
        "stick",
        "canette",
        "zip",
        "joint",
        "pince",
        "papier-gras",
        "mousse",
        "palette",
        "carte",
        "flyer",
        "ruban",
    }
)
GENERIC_KIND = "generique"

KIND_PROMPTS: dict[str, str] = {
    "ampoule": (
        "Product packaging illustration of clear glass pharmaceutical ampoules with plain neck rings, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no needles, no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "barquette": (
        "Product packaging illustration of a rectangular white rigid plastic food tray, empty and plain, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "boite-pliante": (
        "Product packaging illustration of a plain white folding carton box slightly open, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no barcodes, no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "bouchon": (
        "Product packaging illustration of a plain white plastic screw cap with tamper-evident ring, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "caisse": (
        "Product packaging illustration of a brown corrugated shipping carton box, plain, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no barcodes, no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "compte-gouttes": (
        "Product packaging illustration of a glass dropper with black rubber bulb and matte collar, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "etiquette": (
        "Product packaging illustration of a plain wrap-around product label made of abstract color blocks, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no readable text, "
        "no barcodes, no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "film": (
        "Product packaging illustration of a roll of clear plastic packaging film on a cardboard core, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "flacon": (
        "Product packaging illustration of a matte white cylindrical plastic bottle without label, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "generique": (
        "Product packaging illustration of a plain generic packaging component, abstract simple form, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "notice": (
        "Product packaging illustration of a folded paper pharmaceutical leaflet with abstract grey lines only, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no readable text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "opercule": (
        "Product packaging illustration of a silver induction foil seal disc with one peeled corner, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "pompe": (
        "Product packaging illustration of a plain white lotion pump dispenser with short nozzle, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "pot": (
        "Product packaging illustration of a wide white plastic cream jar with matching screw cap beside it, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "sachet": (
        "Product packaging illustration of a matte kraft stand-up pouch with resealable top, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "seau": (
        "Product packaging illustration of a round white plastic industrial bucket with metal wire handle, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "tube": (
        "Product packaging illustration of a white laminate squeeze tube standing on its flip-top cap, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "actionneur": (
        "Product packaging illustration of a plain white aerosol spray actuator button, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "bille": (
        "Product packaging illustration of a plastic roll-on ball and ring applicator, clear and plain, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "stick": (
        "Product packaging illustration of an empty oval white twist-up stick container with raised mechanism, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "canette": (
        "Product packaging illustration of an empty matte silver aluminium aerosol can without label, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "zip": (
        "Product packaging illustration of a close-up of a plain resealable zipper strip on a kraft pouch top, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "joint": (
        "Product packaging illustration of a black rubber round gasket ring, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "pince": (
        "Product packaging illustration of a plain metal locking clip for a bucket lid, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "papier-gras": (
        "Product packaging illustration of a sheet of plain white grease-resistant paper, gently curled, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "mousse": (
        "Product packaging illustration of grey foam dividers in a grid shape, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "palette": (
        "Product packaging illustration of a small plain wooden pallet, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "carte": (
        "Product packaging illustration of a small stack of blank business cards with abstract color blocks, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "flyer": (
        "Product packaging illustration of one blank A5 flyer with abstract sage and terracotta color blocks, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
    "ruban": (
        "Product packaging illustration of a roll of plain brown packaging tape with free end, "
        "clean studio shot, soft diffused light, 3/4 view slightly from above, no brand logos, no text, "
        "no people, no watermark, neutral cream #F6F1EA seamless background, soft contact shadow, "
        "commercial packaging catalog style, high detail, square composition"
    ),
}


def infer_visual_kind(code: str, label: str) -> str:
    text = f"{code} {label}".casefold()
    for needle, kind in VISUAL_RULES:
        if needle in text:
            return kind
    return GENERIC_KIND


def production_status_for(kind: str) -> str:
    if kind in PRODUCED_KINDS:
        return "produced_internal"
    return "to_produce"


def prompt_for_kind(kind: str) -> str:
    return KIND_PROMPTS.get(kind) or KIND_PROMPTS[GENERIC_KIND]
