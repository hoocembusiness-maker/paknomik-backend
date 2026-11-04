"""Load the hosting catalog into an empty database.

    python manage.py import_hosting_catalog

If suppliers already exist, supplier and product rows are left untouched.
Photos and logos from the bundle are still copied. The local SQLite file is never read.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from user.hosting_catalog import import_hosting_catalog


class Command(BaseCommand):
    help = "Import suppliers and products into an empty database. Skip when suppliers already exist."

    def add_arguments(self, parser):
        parser.add_argument(
            "--src",
            default=str(Path(settings.BASE_DIR) / "hosting_catalog"),
            help="Directory produced by export_hosting_catalog.",
        )

    def handle(self, *args, **options):
        report = import_hosting_catalog(Path(options["src"]))
        reason = report.get("reason")
        if reason == "missing":
            self.stdout.write("Aucun catalogue d'hébergement. Base laissée vide.")
            return
        if reason == "suppliers_already_present":
            self.stdout.write(
                "Fournisseurs déjà présents : fiches inchangées. "
                f"Fichiers copiés : {report.get('media_files', 0)}."
            )
            return
        self.stdout.write(self.style.SUCCESS(
            "Import: {suppliers} fournisseur(s), {products} produit(s), "
            "{media_files} fichier(s), {ledger_rows} écriture(s) de bienvenue.".format(**report)
        ))
