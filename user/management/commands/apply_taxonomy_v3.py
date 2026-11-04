"""Apply the Famille → Type de produit taxonomy (idempotent)."""

from django.core.management.base import BaseCommand

from user.taxonomy_v3 import apply_taxonomy_v3


class Command(BaseCommand):
    help = "Apply Paknomik taxonomy v3 (Famille → Type). Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute the migration report then roll back all writes.",
        )

    def handle(self, *args, **options):
        report = apply_taxonomy_v3(dry_run=options["dry_run"])
        self.stdout.write(self.style.SUCCESS(str(report)))
