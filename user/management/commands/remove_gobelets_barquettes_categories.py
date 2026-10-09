"""
Remove gobelet and barquette subcategories from Restauration & Café.

Usage:
  python manage.py remove_gobelets_barquettes_categories --dry-run
  python manage.py remove_gobelets_barquettes_categories
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_remove_gobelets_barquettes import (
    remove_gobelets_barquettes_subcategories,
)


class Command(BaseCommand):
    help = (
        "Delete gobelet and barquette subcategories "
        "(Gobelets plastique/papier, couvercles, barquettes ouvertes)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        with transaction.atomic():
            stats = remove_gobelets_barquettes_subcategories(dry_run=dry_run)
            if dry_run:
                transaction.set_rollback(True)

        if stats.get("skipped"):
            self.stdout.write(self.style.WARNING(f"Skipped: {stats['skipped']}"))
            return

        prefix = "Would " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}remove {stats.get('removed', 0)} categor(ies); "
                f"repoint {stats['products_repointed']} product(s), "
                f"{stats['tender_calls_repointed']} consultation(s); "
                f"update {stats['suppliers_updated']} supplier(s); "
                f"fallback → {stats.get('fallback_subcategory', '—')}"
            )
        )
