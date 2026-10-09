"""
Move gobelet and barquette subcategories under Restauration & Café.

Usage:
  python manage.py move_barquettes_gobelets_to_restauration --dry-run
  python manage.py move_barquettes_gobelets_to_restauration
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_restauration_merge import move_barquettes_gobelets_under_restauration


class Command(BaseCommand):
    help = (
        "Reparent gobelet/barquette subcategories under Restauration & Café "
        "and remove the Barquettes & Gobelets top-level category."
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
            stats = move_barquettes_gobelets_under_restauration(dry_run=dry_run)

            if stats.get("skipped"):
                self.stderr.write(
                    self.style.ERROR(f"Skipped: {stats['skipped']}")
                )
                return

            self.stdout.write(
                self.style.SUCCESS(
                    f"Subcategories moved: {stats['moved']}\n"
                    f"Attribute links copied: {stats['attributes_copied']}\n"
                    f"Products updated: {stats['products_updated']}\n"
                    f"Tender calls updated: {stats['tender_calls_updated']}\n"
                    f"Sourcing requests updated: {stats['sourcing_requests_updated']}\n"
                    f"Old parent removed: {stats['old_parent_removed']}"
                )
            )

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run — no changes saved."))
