"""Write suppliers, their accounts and their products for an empty production database.

    python manage.py export_hosting_catalog
    python manage.py export_hosting_catalog --dest hosting_catalog

Orders, messages, reviews, campaigns and the Pragmadinar ledger are not included.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from user.hosting_catalog import export_hosting_catalog


class Command(BaseCommand):
    help = "Export suppliers and products (no orders, messages or Pragmadinar history)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dest",
            default=str(Path(settings.BASE_DIR) / "hosting_catalog"),
            help="Directory that will receive catalog.json and the product/logo files.",
        )

    def handle(self, *args, **options):
        report = export_hosting_catalog(Path(options["dest"]))
        self.stdout.write(
            "Export: {suppliers} fournisseur(s), {products} produit(s), {media_files} fichier(s).".format(
                **report
            )
        )
        if report["skipped_products"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{report['skipped_products']} produit(s) ignoré(s) : catégorie sans slug."
                )
            )
        self.stdout.write(self.style.SUCCESS(str(Path(options["dest"]))))
