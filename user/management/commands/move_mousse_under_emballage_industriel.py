"""
Move Mousse de protection under Emballage industriel.

Usage:
  python manage.py move_mousse_under_emballage_industriel --dry-run
  python manage.py move_mousse_under_emballage_industriel
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_mousse_merge import move_mousse_under_emballage_industriel


class Command(BaseCommand):
    help = (
        "Reparent Mousse de protection (and its foam subcategories) "
        "under Emballage industriel."
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
            stats = move_mousse_under_emballage_industriel(dry_run=dry_run)

            if stats.get("skipped"):
                self.stderr.write(self.style.ERROR(f"Skipped: {stats['skipped']}"))
                return

            self.stdout.write(
                self.style.SUCCESS(
                    f"Mousse group moved: {stats['moved']}\n"
                    f"Foam subcategories: {stats['foam_subcategories']}\n"
                    f"Products updated: {stats['products_updated']}\n"
                    f"Tender calls updated: {stats['tender_calls_updated']}\n"
                    f"Sourcing requests updated: {stats['sourcing_requests_updated']}\n"
                    f"Suppliers updated: {stats['suppliers_updated']}"
                )
            )

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run — no changes saved."))
