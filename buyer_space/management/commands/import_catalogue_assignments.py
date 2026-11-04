from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from buyer_space.catalogue_assignments import import_catalogue_assignments


class Command(BaseCommand):
    help = (
        "Importe la multi-affectation produits ↔ kits depuis "
        "docs/catalogue_multi_affectation.xlsx."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default="",
            help="Classeur .xlsx. Défaut : docs/catalogue_multi_affectation.xlsx.",
        )

    def handle(self, *args, **options):
        raw = options["path"] or str(
            Path(settings.BASE_DIR) / "docs" / "catalogue_multi_affectation.xlsx"
        )
        path = Path(raw)
        if not path.is_file():
            raise CommandError(f"Fichier introuvable : {path}")
        try:
            report = import_catalogue_assignments(path)
        except (KeyError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "Secteurs créés: {sectors_created} · "
            "Produits créés: {products_created} · "
            "Produits mis à jour: {products_updated} · "
            "Kits créés: {kits_created}".format(**report)
        )
        self.stdout.write(
            "Fiches kit: {sheets} · "
            "Affectations: {assignments} "
            "(principaux {principals}, alternatifs {alternatives}) · "
            "Audits: {audits}".format(**report)
        )
        self.stdout.write(self.style.SUCCESS(f"Import terminé : {path}"))
