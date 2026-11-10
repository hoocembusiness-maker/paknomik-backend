"""
Assure Matière, Dimensions et Fermeture sur chaque type d'emballage marché.

Usage:
  py -3 manage.py seed_core_packaging_attributes --dry-run
  py -3 manage.py seed_core_packaging_attributes
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.core_packaging_attributes import (
    preview_core_packaging_attributes,
    seed_core_packaging_attributes,
)


class Command(BaseCommand):
    help = (
        "Ajoute les caractéristiques techniques minimales "
        "(Matière, Dimensions, Fermeture) aux types d'emballage qui en manquent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche le volume de liens à créer sans écrire en base.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        with transaction.atomic():
            stats = (
                preview_core_packaging_attributes()
                if dry_run
                else seed_core_packaging_attributes()
            )
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                "Types scannés : {categories_scanned} · "
                "attributs cœur : {attributes_ensured} · "
                "liens créés : {links_created} · "
                "déjà complets : {skipped_complete}".format(**stats.as_dict())
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — aucune écriture."))
