"""Import idempotent du classeur PakSuites."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.utils.text import slugify

from buyer_space.catalogue_models import (
    SuiteCompatibility,
    SuiteComponentDefinition,
    SuiteComponentLine,
    SuiteEnsemble,
    SuiteSource,
    SuiteTaxon,
    SuiteVariant,
    publish_blockers,
)


@dataclass
class ImportReport:
    ensembles_created: int = 0
    ensembles_updated: int = 0
    lines_written: int = 0
    sources_written: int = 0
    types_written: int = 0
    kept_editorial: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Ensembles créés : {self.ensembles_created}",
            f"Ensembles mis à jour : {self.ensembles_updated}",
            f"Lignes de composants écrites : {self.lines_written}",
            f"Sources écrites : {self.sources_written}",
            f"Types écrits : {self.types_written}",
            f"Statuts éditoriaux conservés : {self.kept_editorial}",
        ]
        if self.errors:
            lines.append(f"Erreurs : {len(self.errors)}")
            lines.extend(f"- {item}" for item in self.errors)
        return "\n".join(lines)


def _cell(row, index):
    if index is None or index >= len(row):
        return ""
    value = row[index]
    if value is None:
        return ""
    text = str(value).strip()
    if text.startswith("="):
        return ""
    return text


def _headers(row):
    return {str(cell).strip(): index for index, cell in enumerate(row) if cell}


def _parse_confidence(text: str) -> str:
    raw = (text or "").strip().lower()
    if raw.startswith("hypoth"):
        return SuiteEnsemble.Confidence.HYPOTHESIS
    if raw.startswith("déduit") or raw.startswith("deduit"):
        return SuiteEnsemble.Confidence.INFERRED
    if raw.startswith("confirm"):
        return SuiteEnsemble.Confidence.CONFIRMED
    return SuiteEnsemble.Confidence.INFERRED


def _parse_status(text: str) -> str:
    raw = (text or "").strip().lower()
    if raw.startswith("oblig"):
        return SuiteComponentLine.Status.REQUIRED
    if raw.startswith("option"):
        return SuiteComponentLine.Status.OPTIONAL
    if "selon" in raw:
        return SuiteComponentLine.Status.DEPENDS
    return SuiteComponentLine.Status.TO_CONFIRM


def _parse_level(role: str, type_label: str, function_label: str) -> str:
    role_l = (role or "").strip().lower()
    type_l = (type_label or "").strip().lower()
    if role_l == "primaire":
        return SuiteComponentLine.Level.PRIMARY
    if role_l == "secondaire":
        return SuiteComponentLine.Level.SECONDARY
    if role_l == "tertiaire":
        return SuiteComponentLine.Level.TERTIARY
    blob = f"{role_l} {type_l}"
    if any(token in blob for token in ("caisse", "palett", "tertiaire", "logistique")):
        return SuiteComponentLine.Level.TERTIARY
    if any(token in type_l for token in ("étui", "etui", "notice", "boîte", "boite", "coffret")):
        return SuiteComponentLine.Level.SECONDARY
    function = (function_label or "").strip().lower()
    if function.startswith("tertiaire"):
        return SuiteComponentLine.Level.TERTIARY
    if function.startswith("secondaire"):
        return SuiteComponentLine.Level.SECONDARY
    return SuiteComponentLine.Level.PRIMARY


def _split_multi(text: str) -> list[str]:
    return [part.strip() for part in (text or "").replace("；", ";").split(";") if part.strip()]


def _taxon(kind, code, label, parent=None, note="", origin=""):
    taxon, _created = SuiteTaxon.objects.update_or_create(
        code=code,
        defaults={
            "kind": kind,
            "label_fr": label[:200],
            "parent": parent,
            "note": note,
            "origin": origin[:80],
        },
    )
    return taxon


def _variants_from_note(ensemble, note: str):
    """Ne crée une variante que si le classeur décrit une différence réelle."""
    raw = (note or "").strip()
    if not raw or "confirmer" in raw.lower():
        return
    markers = (
        ("éco", SuiteVariant.Tier.ECONOMY),
        ("eco", SuiteVariant.Tier.ECONOMY),
        ("standard", SuiteVariant.Tier.STANDARD),
        ("premium", SuiteVariant.Tier.PREMIUM),
    )
    lowered = raw.lower()
    found = []
    for token, tier in markers:
        if token in lowered and "à confirmer" not in lowered:
            found.append((tier, raw))
    # Un seul bloc sans distinction n'est pas trois variantes.
    distinct = {tier for tier, _summary in found}
    if len(distinct) < 2:
        return
    for tier in distinct:
        SuiteVariant.objects.update_or_create(
            ensemble=ensemble,
            tier=tier,
            defaults={"summary": raw[:2000]},
        )


def import_catalogue(path: str | Path) -> ImportReport:
    import openpyxl

    report = ImportReport()
    workbook = openpyxl.load_workbook(Path(path), data_only=False)
    required = {"Ensembles", "Composants", "Sources", "Taxonomie"}
    missing = required - set(workbook.sheetnames)
    if missing:
        report.errors.append("Feuilles manquantes : " + ", ".join(sorted(missing)))
        return report

    with transaction.atomic():
        _import_sources(workbook["Sources"], report)
        families = _import_taxonomy(workbook["Taxonomie"], report)
        _import_ensembles(workbook["Ensembles"], workbook["Composants"], families, report)
    return report


def _import_sources(sheet, report: ImportReport):
    headers = _headers(next(sheet.iter_rows(max_row=1, values_only=True)))
    for row in sheet.iter_rows(min_row=2, values_only=True):
        code = _cell(row, headers.get("ID"))
        name = _cell(row, headers.get("Source"))
        if not code or not name:
            continue
        SuiteSource.objects.update_or_create(
            code=code,
            defaults={
                "name": name[:240],
                "url": _cell(row, headers.get("URL"))[:500],
                "info_type": _cell(row, headers.get("Type d'information exploité"))[:240],
                "market": _cell(row, headers.get("Pays / marché"))[:120],
            },
        )
        report.sources_written += 1


def _import_taxonomy(sheet, report: ImportReport):
    headers = _headers(next(sheet.iter_rows(max_row=1, values_only=True)))
    families = {}
    for row in sheet.iter_rows(min_row=2, values_only=True):
        letter = _cell(row, headers.get("Famille"))
        label = _cell(row, headers.get("Libellé famille"))
        type_label = _cell(row, headers.get("Type"))
        if not letter or not type_label:
            continue
        family = families.get(letter)
        if family is None:
            family = _taxon(
                SuiteTaxon.Kind.FAMILY,
                f"F-{letter}",
                label or letter,
                origin=_cell(row, headers.get("Origine")),
            )
            families[letter] = family
        code = slugify(type_label)[:70] or f"type-{letter}"
        if SuiteTaxon.objects.exclude(parent=family).filter(code=code).exists():
            code = f"{code}-{letter.lower()}"[:80]
        _taxon(
            SuiteTaxon.Kind.TYPE,
            code,
            type_label,
            parent=family,
            note=_cell(row, headers.get("Observation d'audit")),
            origin=_cell(row, headers.get("Origine")),
        )
        report.types_written += 1
    return families


def _import_ensembles(sheet, components, families, report: ImportReport):
    headers = _headers(next(sheet.iter_rows(max_row=1, values_only=True)))
    component_headers = _headers(next(components.iter_rows(max_row=1, values_only=True)))
    grouped: dict[str, list] = {}
    for row in components.iter_rows(min_row=2, values_only=True):
        code = _cell(row, component_headers.get("ID ensemble"))
        if code:
            grouped.setdefault(code, []).append(row)

    for row in sheet.iter_rows(min_row=2, values_only=True):
        code = _cell(row, headers.get("ID"))
        name = _cell(row, headers.get("Nom générique"))
        if not code and not name:
            continue
        if not code or not name:
            report.errors.append(f"Ensemble incomplet ignoré : {code or name}")
            continue
        slug = slugify(code)[:80]
        defaults = {
            "name": name[:240],
            "slug": slug,
            "description": _cell(row, headers.get("Description")),
            "priority": _cell(row, headers.get("Priorité"))[:8],
            "target_product": _cell(row, headers.get("Produit cible"))[:300],
            "function_label": _cell(row, headers.get("Fonction"))[:200],
            "confidence": _parse_confidence(_cell(row, headers.get("Niveau de confiance"))),
            "sourced_facts": _cell(row, headers.get("Faits sourcés")),
            "points_to_confirm": _cell(row, headers.get("Points à confirmer")),
            "algeria_relevance": _cell(row, headers.get("Pertinence Algérie")),
            "variant_note": _cell(row, headers.get("Variantes éco / standard / premium")),
        }
        function = _taxon(
            SuiteTaxon.Kind.FUNCTION,
            f"FN-{slug}"[:80],
            defaults["function_label"] or "Fonction à préciser",
        )
        defaults["function"] = function
        existing = SuiteEnsemble.objects.filter(code=code).first()
        if existing:
            for key, value in defaults.items():
                setattr(existing, key, value)
            existing.save()
            report.ensembles_updated += 1
            report.kept_editorial += 1
            ensemble = existing
        else:
            ensemble = SuiteEnsemble.objects.create(code=code, editorial_status=SuiteEnsemble.Status.DRAFT, **defaults)
            report.ensembles_created += 1

        sector_ids = []
        for label in _split_multi(_cell(row, headers.get("Secteurs"))):
            sector = _taxon(SuiteTaxon.Kind.SECTOR, f"S-{slugify(label)}"[:80], label)
            sector_ids.append(sector.id)
        ensemble.sectors.set(sector_ids)
        source_codes = _split_multi(_cell(row, headers.get("Sources")))
        ensemble.sources.set(SuiteSource.objects.filter(code__in=source_codes))
        _write_lines(ensemble, grouped.get(code, []), component_headers, families, report)
        _variants_from_note(ensemble, ensemble.variant_note)
        if existing is None and not publish_blockers(ensemble):
            ensemble.editorial_status = SuiteEnsemble.Status.PUBLISHED
            ensemble.save(update_fields=["editorial_status", "updated_at"])


# When the Excel "Famille" cell is empty, map type labels to the right letter.
TYPE_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("joint", "H"),
    ("pince", "H"),
    ("actionneur", "H"),
    ("valve", "H"),
    ("pompe", "H"),
    ("bouchon", "H"),
    ("opercule", "H"),
    ("robinet", "H"),
    ("fermeture zip", "H"),
    ("zip", "H"),
    ("bille", "H"),
    ("roll-on", "H"),
    ("gobelet doseur", "H"),
    ("ruban", "K"),
    ("adhésif", "K"),
    ("adhesif", "K"),
    ("palette", "K"),
    ("calage", "K"),
    ("couvercle de seau", "K"),
    ("mousse", "K"),
    ("film", "I"),
    ("papier", "J"),
    ("notice", "L"),
    ("flyer", "L"),
    ("carte de visite", "L"),
    ("étui", "A"),
    ("etui", "A"),
    ("boîte à ampoules", "A"),
    ("boite a ampoules", "A"),
    ("stick", "C"),
    ("twist-up", "C"),
)


def _infer_family_letter(type_label: str, explicit: str) -> str:
    letter = (explicit or "").strip().upper()
    if letter and letter != "X":
        return letter
    text = (type_label or "").casefold()
    for needle, hinted in TYPE_FAMILY_HINTS:
        if needle in text:
            return hinted
    return letter


def _write_lines(ensemble, rows, headers, families, report: ImportReport):
    seen = []
    for index, row in enumerate(rows):
        type_label = _cell(row, headers.get("Type exact (taxonomie)"))
        role = _cell(row, headers.get("Rôle"))
        if not type_label or not role:
            report.errors.append(f"{ensemble.code} : composant sans type ou sans rôle, ignoré.")
            continue
        family_letter = _infer_family_letter(
            type_label, _cell(row, headers.get("Famille"))
        )
        family = families.get(family_letter)
        type_code = slugify(type_label)[:70] or "type"
        taxon = None
        if family is not None:
            taxon = SuiteTaxon.objects.filter(
                kind=SuiteTaxon.Kind.TYPE, parent=family, label_fr=type_label
            ).first()
        if taxon is None:
            taxon = SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.TYPE, code=type_code, label_fr=type_label).first()
        if taxon is None:
            if family is None:
                family = _taxon(SuiteTaxon.Kind.FAMILY, "F-X", "Famille à classer")
            taxon = _taxon(
                SuiteTaxon.Kind.TYPE,
                f"{type_code}-{family_letter.lower()}"[:80] if family_letter else type_code,
                type_label,
                parent=family,
                origin="Composant",
            )
        definition, _created = SuiteComponentDefinition.objects.get_or_create(
            code=(taxon.code or type_code)[:120],
            defaults={"label": type_label[:240], "taxon": taxon},
        )
        if definition.label != type_label or definition.taxon_id != taxon.id:
            definition.label = type_label[:240]
            definition.taxon = taxon
            definition.save(update_fields=["label", "taxon"])
        level = _parse_level(role, type_label, ensemble.function_label)
        status = _parse_status(_cell(row, headers.get("Statut")))
        payload = {
            "definition": definition,
            "taxon": taxon,
            "level": level,
            "status": status,
            "material": _cell(row, headers.get("Matériau envisageable"))[:240],
            "technical_notes": _cell(row, headers.get("Caractéristiques techniques clés")),
            "compatibility_notes": _cell(row, headers.get("Compatibilité à vérifier")),
            "sort_order": index,
        }
        line = (
            SuiteComponentLine.objects.filter(ensemble=ensemble, definition=definition, role=role)
            .exclude(pk__in=seen)
            .first()
        )
        if line is None:
            line = SuiteComponentLine.objects.create(ensemble=ensemble, role=role[:120], **payload)
        else:
            for key, value in payload.items():
                setattr(line, key, value)
            line.role = role[:120]
            line.save()
        seen.append(line.id)
        justification = payload["compatibility_notes"]
        if justification:
            SuiteCompatibility.objects.update_or_create(
                subject=line,
                counterpart=None,
                defaults={
                    "status": SuiteCompatibility.Status.INFERRED,
                    "justification": justification,
                    "verified_on": None,
                },
            )
        report.lines_written += 1
