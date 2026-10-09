"""
Remove legacy duplicate L1 categories from supplier profile interest pickers.

Usage:
  python manage.py cleanup_duplicate_interest_categories --dry-run
  python manage.py cleanup_duplicate_interest_categories
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_duplicate_category_cleanup import cleanup_duplicate_interest_categories
from user.models import Category, Product, Supplier, TenderCall


class Command(BaseCommand):
    help = (
        "Merge slugless legacy interest categories into their v2 canonical "
        "counterparts and delete the duplicates."
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
            stats = cleanup_duplicate_interest_categories(
                Category=Category,
                Product=Product,
                Supplier=Supplier,
                SourcingRequest=TenderCall,
                dry_run=dry_run,
            )
            if dry_run:
                transaction.set_rollback(True)

        if not stats["merged"]:
            self.stdout.write(
                self.style.WARNING("No duplicate interest categories found.")
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {len(stats['merged'])} duplicate categor"
                f"{'y' if len(stats['merged']) == 1 else 'ies'}."
            )
        )
        for entry in stats["merged"]:
            self.stdout.write(
                f"  {entry['legacy_name']} (id={entry['legacy_id']}) "
                f"-> {entry['canonical_slug']} (id={entry['canonical_id']}): "
                f"{entry['products']} product(s), "
                f"{entry['suppliers']} supplier(s), "
                f"{entry['sourcing_requests']} sourcing request(s)"
            )
        self.stdout.write(
            f"Totals: products={stats['products_repointed']}, "
            f"suppliers={stats['suppliers_updated']}, "
            f"sourcing={stats['sourcing_requests_repointed']}, "
            f"deleted={stats['deleted']}"
        )
        if stats["skipped"]:
            self.stdout.write(
                self.style.WARNING(f"Skipped: {stats['skipped']}")
            )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))
