"""Update technical attributes for bottles, cans, industrial packs and labels.

Usage:
  python manage.py seed_container_label_attributes --dry-run
  python manage.py seed_container_label_attributes
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_family_attributes import apply_container_and_label_attributes


class Command(BaseCommand):
    help = (
        "Met à jour les caractéristiques de Bouteilles, Canettes, "
        "Emballage industriel et Étiquettes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Vérifie les changements sans les enregistrer",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            apply_container_and_label_attributes()
            if options["dry_run"]:
                transaction.set_rollback(True)
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — aucun changement enregistré."))
        else:
            self.stdout.write(self.style.SUCCESS("Caractéristiques mises à jour."))
