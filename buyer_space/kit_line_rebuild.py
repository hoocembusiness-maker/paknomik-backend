"""Reconstruit les lignes des kits du dictionnaire qui n'ont aucun composant.

Les kits déjà structurés par le catalogue PakSuites ne sont pas modifiés.
Un composant est rattaché à un type existant quand le libellé correspond.
Sinon le type est ajouté dans la famille la plus proche.
"""

from __future__ import annotations

import unicodedata

from django.db import transaction
from django.utils.text import slugify

from buyer_space.catalogue_models import (
    SuiteComponentDefinition,
    SuiteComponentLine,
    SuiteEnsemble,
    SuiteTaxon,
)
from buyer_space.models import PackagingKit
from buyer_space.suite_visuals import infer_visual_kind

# (mots-clés, code de type existant ou "", famille, rôle)
# L'ordre compte : le premier mot-clé trouvé l'emporte.
TYPE_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("compte-gouttes", "pipette"), "fermeture-compte-gouttes", "F-H", "Distribution"),
    (("gachette",), "flacon-pulverisateur", "F-C", "Distribution"),
    (("bec verseur", "robinet"), "robinet-bec-verseur", "F-H", "Distribution"),
    (("pompe",), "pompe-a-lotion-pompe-doseuse", "F-H", "Distribution"),
    (("valve de degazage",), "valve-de-degazage-type-a-creer-h", "F-H", "Dégazage"),
    (("fermeture zip", "zip refermable", "sachets pe zip"), "fermeture-zip-type-a-creer-h", "F-H", "Refermeture"),
    (("bouchon pehd",), "bouchon-pehd", "F-H", "Fermeture"),
    (("bouchon securite enfant", "bouchon elastomere"), "bouchon-de-securite-enfant-crc-a-inviolabilite", "F-H", "Fermeture"),
    (("bouchon spray",), "bouchon-spray-pulverisateur", "F-H", "Fermeture"),
    (("flip-top", "flip top"), "bouchon-flip-top-a-vis-h", "F-H", "Fermeture"),
    (("bouchon", "capuchon", "capsule"), "bouchon-a-vis-en-plastique", "F-H", "Fermeture"),
    (("opercule", "couvercle"), "opercule-liner-a-induction", "F-H", "Fermeture"),
    (("etiquette auto-adhesive", "auto-adhesive", "auto adhesive"), "etiquette-auto-adhesive", "F-G", "Identification"),
    (("manchon", "thermoretractable", "wrap-around"), "etiquette-thermoretractable-manchon", "F-G", "Identification"),
    (("etiquette", "sticker", "banderole"), "etiquette-g", "F-G", "Identification"),
    (("notice",), "notice-imprime-pharmaceutique", "F-L", "Identification"),
    (("impression",), "impression-sur-emballage-souple", "F-L", "Identification"),
    (("film a bulles", "film a bulles"), "film-a-bulles", "F-I", "Protection"),
    (("film etirable", "film etirable"), "film-etirable", "F-I", "Protection"),
    (("film de fardelage",), "film-de-fardelage", "F-I", "Protection"),
    (("film de scellage", "film opercule", "opercule aluminium"), "film-de-scellage-lidding", "F-I", "Fermeture"),
    (("canette",), "canette-en-aluminium", "F-E", "Contenant"),
    (("big bag",), "big-bag", "F-B", "Contenant"),
    (("doypack", "pouch"), "sachet-doypack", "F-B", "Contenant"),
    (("sachet sous vide", "sachet barriere"), "sachet-barriere", "F-B", "Contenant"),
    (("sachet", "pochette"), "sachet-plat", "F-B", "Contenant"),
    (("sac papier", "sac kraft"), "sac-en-papier", "F-B", "Contenant"),
    (("sac ", "sac pe"), "sac-plastique", "F-B", "Contenant"),
    (("papier de soie",), "papier-de-soie", "F-J", "Protection"),
    (("papier sulfurise", "anti-graisse", "resistant aux graisses"), "papier-resistant-aux-graisses-sulfurise", "F-J", "Protection"),
    (("caisse carton", "caisse "), "caisse-carton", "F-A", "Tertiaire"),
    (("boite postale", "mailer"), "mailer-boite-postale", "F-A", "Tertiaire"),
    (("coffret",), "coffret", "F-A", "Secondaire"),
    (("etui pharmaceutique",), "etui-pharmaceutique", "F-A", "Secondaire"),
    (("etui", "fourreau", "sleeve"), "boite-pliante", "F-A", "Secondaire"),
    (("boite", "carton"), "boite-pliante", "F-A", "Secondaire"),
    (("barquette",), "barquette", "F-D", "Contenant"),
    (("gobelet",), "gobelet", "F-D", "Contenant"),
    (("pot verre", "pot en verre"), "pot-en-verre", "F-D", "Contenant"),
    (("pot ", "pot carton", "mini pot"), "pot-en-plastique", "F-D", "Contenant"),
    (("flacon compte-gouttes",), "flacon-compte-gouttes", "F-C", "Contenant"),
    (("flacon pulverisateur",), "flacon-pulverisateur", "F-C", "Contenant"),
    (("flacon verre", "flacon en verre", "bouteille en verre"), "bouteille-en-verre", "F-C", "Contenant"),
    (("bouteille pet ou pp", "pp/pehd", "pp / pehd", "bouteille pp"), "bouteille-pet-ou-pppehd", "F-C", "Contenant"),
    (("bouteille pet", "flacon pet"), "bouteille-pet", "F-C", "Contenant"),
    (("bouteille", "flacon"), "flacon-en-plastique", "F-C", "Contenant"),
    (("tube mascara", "brosse"), "tube-mascara", "F-F", "Contenant"),
    (("tube souple",), "tube-souple-lamine", "F-F", "Contenant"),
    (("tube",), "tube-en-plastique", "F-F", "Contenant"),
    (("bidon",), "bidon-en-plastique", "F-K", "Contenant"),
    (("jerricane", "jerrican"), "jerricane", "F-K", "Contenant"),
    (("fut ", "fut pehd", "fût"), "fut-industriel", "F-K", "Contenant"),
    (("seau",), "seau-industriel", "F-K", "Contenant"),
    (("mousse", "calage"), "mousse-de-protection", "F-K", "Protection"),
    (("ruban", "cerclage", "bande adhesive"), "ruban-adhesif-scelle-cerclage", "F-K", "Fermeture"),
    (("film",), "film-alimentaire", "F-I", "Protection"),
    (("papier",), "papier-kraft-en-rouleau", "F-J", "Protection"),
)

FAMILY_FALLBACK: tuple[tuple[tuple[str, ...], str], ...] = (
    (("etiquette", "impression", "notice"), "F-L"),
    (("bouchon", "couvercle", "zip", "valve", "joint", "clip"), "F-H"),
    (("sachet", "sac", "pochette"), "F-B"),
    (("boite", "carton", "caisse", "etui"), "F-A"),
    (("bouteille", "flacon"), "F-C"),
    (("pot", "barquette", "gobelet"), "F-D"),
    (("canette", "metal", "fer-blanc"), "F-E"),
    (("tube",), "F-F"),
    (("film",), "F-I"),
    (("papier",), "F-J"),
    (("ruban", "palette", "calage"), "F-K"),
)


def rebuild_empty_dictionary_kits() -> dict[str, int]:
    """Ajoute les composants des kits du dictionnaire qui n'en ont aucun."""

    report = {"kits": 0, "lines": 0, "types_created": 0, "types_reused": 0, "skipped": 0}
    sheets = PackagingKit.objects.select_related("ensemble").order_by("ensemble__code")
    with transaction.atomic():
        for sheet in sheets:
            ensemble = sheet.ensemble
            if ensemble.lines.exists():
                report["skipped"] += 1
                continue
            parts = _parts(sheet.components_label)
            if not parts:
                report["skipped"] += 1
                continue
            for index, label in enumerate(parts):
                taxon, role, created = _resolve_type(label)
                if created:
                    report["types_created"] += 1
                else:
                    report["types_reused"] += 1
                definition = _definition_for(taxon)
                SuiteComponentLine.objects.create(
                    ensemble=ensemble,
                    definition=definition,
                    taxon=taxon,
                    role=role[:120],
                    level=_level(role, index),
                    status=SuiteComponentLine.Status.REQUIRED,
                    technical_notes=label if label != taxon.label_fr else "",
                    sort_order=index,
                )
                report["lines"] += 1
            report["kits"] += 1
    return report


def _resolve_type(label: str) -> tuple[SuiteTaxon, str, bool]:
    folded = _fold(label)
    existing = _existing_type(folded)
    if existing is not None:
        return existing, _role_for(folded, "Contenant"), False
    for needles, code, family_code, role in TYPE_RULES:
        if any(needle in folded for needle in needles):
            taxon = SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.TYPE, code=code).first()
            if taxon is None:
                taxon, created = _create_type(label, family_code)
                return taxon, role, created
            return taxon, role, False
    family_code = "F-X"
    for needles, hinted in FAMILY_FALLBACK:
        if any(needle in folded for needle in needles):
            family_code = hinted
            break
    taxon, created = _create_type(label, family_code)
    return taxon, _role_for(folded, "Accessoire"), created


def _existing_type(folded: str) -> SuiteTaxon | None:
    for taxon in SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.TYPE):
        if _fold(taxon.label_fr) == folded:
            return taxon
    return None


def _create_type(label: str, family_code: str) -> tuple[SuiteTaxon, bool]:
    family = _family(family_code)
    code = (slugify(label) or "type")[:70]
    current = SuiteTaxon.objects.filter(code=code).first()
    if current is not None and current.kind == SuiteTaxon.Kind.TYPE:
        return current, False
    if current is not None:
        code = f"{code}-{family_code[-1].lower()}"[:80]
    taxon = SuiteTaxon.objects.create(
        kind=SuiteTaxon.Kind.TYPE,
        code=code,
        label_fr=label[:200],
        parent=family,
        origin="catalogue_multi_affectation",
    )
    return taxon, True


def _family(code: str) -> SuiteTaxon:
    family = SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.FAMILY, code=code).first()
    if family is not None:
        return family
    labels = {
        "F-X": "Famille à classer",
    }
    return SuiteTaxon.objects.create(
        kind=SuiteTaxon.Kind.FAMILY,
        code=code,
        label_fr=labels.get(code, code),
        origin="catalogue_multi_affectation",
    )


def _definition_for(taxon: SuiteTaxon) -> SuiteComponentDefinition:
    kind = infer_visual_kind(taxon.code, taxon.label_fr)
    definition, _created = SuiteComponentDefinition.objects.get_or_create(
        code=taxon.code[:120],
        defaults={"label": taxon.label_fr[:240], "taxon": taxon, "visual_kind": kind},
    )
    updates = []
    if definition.taxon_id != taxon.id:
        definition.taxon = taxon
        updates.append("taxon")
    if not definition.visual_kind:
        definition.visual_kind = kind
        updates.append("visual_kind")
    if updates:
        definition.save(update_fields=updates)
    return definition


def _role_for(folded: str, default: str) -> str:
    for needles, _code, _family, role in TYPE_RULES:
        if any(needle in folded for needle in needles):
            return role
    return default


def _level(role: str, index: int) -> str:
    if role in {"Tertiaire", "Protection"}:
        return SuiteComponentLine.Level.TERTIARY
    if role == "Secondaire":
        return SuiteComponentLine.Level.SECONDARY
    if index == 0:
        return SuiteComponentLine.Level.PRIMARY
    return SuiteComponentLine.Level.PRIMARY


def _parts(text: str) -> list[str]:
    return [part.strip() for part in (text or "").split("|") if part.strip()]


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(stripped.casefold().replace("«", " ").replace("»", " ").split())
