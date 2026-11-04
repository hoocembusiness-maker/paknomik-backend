import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from buyer_space.models import LibraryProduct
from buyer_space.product_library import sync_library

DOCS = Path(__file__).resolve().parents[3] / "docs"


class Command(BaseCommand):
    help = (
        "Synchronise la bibliothèque produits (secteurs + templates) "
        "et exporte docs/buyer_product_library_prompts.csv pour Claude."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--overwrite-prompts",
            action="store_true",
            help="Réécrit les prompts même s'ils existent déjà.",
        )
        parser.add_argument(
            "--export-only",
            action="store_true",
            help="N'exporte que le CSV, sans sync.",
        )

    def handle(self, *args, **options):
        if not options["export_only"]:
            report = sync_library(overwrite_prompts=options["overwrite_prompts"])
            self.stdout.write(
                f"Secteurs créés: {report['sectors']} · "
                f"Produits créés: {report['products_created']} · "
                f"Kits recommandés liés: {report.get('recommended_linked', 0)}"
            )

        out = DOCS / "buyer_product_library_prompts.csv"
        DOCS.mkdir(parents=True, exist_ok=True)
        rows = LibraryProduct.objects.select_related("sector").filter(is_active=True)
        with out.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "code",
                    "nom",
                    "secteur",
                    "fichier",
                    "brief",
                    "prompt",
                    "a_eviter",
                ],
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()
            for product in rows:
                writer.writerow(
                    {
                        "code": product.code,
                        "nom": product.name,
                        "secteur": product.sector.label_fr,
                        "fichier": product.suggested_filename
                        or f"prod-{product.code}.jpg",
                        "brief": product.description,
                        "prompt": product.generation_prompt,
                        "a_eviter": "marques; logos; texte lisible; codes-barres; personnes",
                    }
                )
        self.stdout.write(self.style.SUCCESS(f"CSV: {out} ({rows.count()} produits)"))
        self.stdout.write(
            "Prompt Claude: générer une image 1:1 par ligne (fond #F6F1EA), "
            "même logique que les types d'emballage PakSuites."
        )
