"""
Seed the Boîte compartimentée type, layout filter, and Pro Box dimensions.

Usage:
  py -3 manage.py seed_boite_compartiment --dry-run
  py -3 manage.py seed_boite_compartiment
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.boite_compartiment import seed_boite_compartiment


class Command(BaseCommand):
    help = (
        "Ajoute le type Boîte compartimentée, la répartition des compartiments, "
        "et les dimensions des produits Pro Box."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche le volume de mises à jour sans écrire en base.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        with transaction.atomic():
            stats = seed_boite_compartiment()
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                "Type Boîte compartimentée · "
                f"options : {stats.get('options', 0)} · "
                f"fournisseurs : {stats.get('suppliers', 0)} · "
                f"produits mis à jour : {stats.get('updated', 0)} · "
                f"ignorés : {stats.get('skipped', 0)}"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — aucune écriture."))
