"""Import PakSuites visual production CSVs (docs/) into SuiteImage rows."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.utils.text import slugify

from buyer_space.catalogue_models import SuiteEnsemble, SuiteImage

OVERVIEW_VIEW = "overview"
COMPOSANT_VIEW_PREFIX = "composant:"

# CSV piece label → visual_kind slug (aligned with suite_visuals / frontend)
PIECE_TO_KIND: dict[str, str] = {
    "papier gras": "papier-gras",
    "ruban adhesif": "ruban",
    "ruban adhésif": "ruban",
    "zip": "zip",
    "bille roll-on": "bille",
    "stick twist-up": "stick",
    "canette alu": "canette",
    "actionneur": "actionneur",
    "joint": "joint",
    "pince": "pince",
    "mousse": "mousse",
    "palette": "palette",
    "carte de visite": "carte",
    "flyer": "flyer",
}


@dataclass
class VisualCsvReport:
    overview_created: int = 0
    overview_updated: int = 0
    composant_created: int = 0
    composant_updated: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Overview: {self.overview_created} créées, {self.overview_updated} mises à jour",
            (
                f"Composants: {self.composant_created} créées, "
                f"{self.composant_updated} mises à jour"
            ),
        ]
        if self.skipped:
            lines.append(f"Ignorés: {len(self.skipped)}")
            lines.extend(f"  - {s}" for s in self.skipped[:30])
        if self.errors:
            lines.append(f"Erreurs: {len(self.errors)}")
            lines.extend(f"  - {e}" for e in self.errors)
        return "\n".join(lines)


def _normalize_overview_filename(raw: str, code: str) -> str:
    name = (raw or "").strip()
    if not name:
        name = f"{code.lower()}-overview.png"
    # Fix accidental double prefix: pks-pks-bak-001 → pks-bak-001
    name = re.sub(r"^pks-pks-", "pks-", name, count=1, flags=re.IGNORECASE)
    return name


def _piece_kind(piece: str) -> str:
    key = (piece or "").strip().casefold()
    if key in PIECE_TO_KIND:
        return PIECE_TO_KIND[key]
    return slugify(piece) or "generique"


def _upsert_image(
    *,
    ensemble: SuiteEnsemble,
    view_type: str,
    filename: str,
    prompt: str,
    report: VisualCsvReport,
    bucket: str,
) -> None:
    defaults = {
        "suggested_filename": filename,
        "generation_prompt": prompt.strip(),
        "production_status": SuiteImage.ProductionStatus.TO_PRODUCE,
        "license": SuiteImage.License.PAKNOMIK,
        "illustrative": True,
        "rights": SuiteImage.Rights.TO_VERIFY,
    }
    existing = SuiteImage.objects.filter(ensemble=ensemble, view_type=view_type).first()
    if existing:
        for key, value in defaults.items():
            setattr(existing, key, value)
        existing.save(
            update_fields=[
                "suggested_filename",
                "generation_prompt",
                "production_status",
                "license",
                "illustrative",
                "rights",
            ]
        )
        if bucket == "overview":
            report.overview_updated += 1
        else:
            report.composant_updated += 1
        return

    SuiteImage.objects.create(ensemble=ensemble, view_type=view_type, **defaults)
    if bucket == "overview":
        report.overview_created += 1
    else:
        report.composant_created += 1


@transaction.atomic
def import_visual_csvs(
    overview_path: Path,
    composants_path: Path | None = None,
) -> VisualCsvReport:
    report = VisualCsvReport()

    if not overview_path.exists():
        report.errors.append(f"Fichier introuvable: {overview_path}")
        return report

    with overview_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            ensemble = SuiteEnsemble.objects.filter(code=code).first()
            if not ensemble:
                report.skipped.append(f"Suite inconnue: {code}")
                continue
            prompt = (row.get("prompt") or "").strip()
            if not prompt:
                report.skipped.append(f"Prompt vide: {code}")
                continue
            filename = _normalize_overview_filename(row.get("fichier") or "", code)
            _upsert_image(
                ensemble=ensemble,
                view_type=OVERVIEW_VIEW,
                filename=filename,
                prompt=prompt,
                report=report,
                bucket="overview",
            )

    if composants_path is None:
        return report
    if not composants_path.exists():
        report.errors.append(f"Fichier introuvable: {composants_path}")
        return report

    with composants_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            piece = (row.get("piece") or "").strip()
            code = (row.get("suite") or "").strip()
            if not piece or not code:
                continue
            ensemble = SuiteEnsemble.objects.filter(code=code).first()
            if not ensemble:
                report.skipped.append(f"Suite inconnue (composant {piece}): {code}")
                continue
            prompt = (row.get("prompt") or "").strip()
            if not prompt:
                report.skipped.append(f"Prompt composant vide: {piece}")
                continue
            kind = _piece_kind(piece)
            filename = (row.get("fichier") or "").strip() or f"pks-comp-{kind}.png"
            view_type = f"{COMPOSANT_VIEW_PREFIX}{kind}"
            _upsert_image(
                ensemble=ensemble,
                view_type=view_type,
                filename=filename,
                prompt=prompt,
                report=report,
                bucket="composant",
            )

    return report
