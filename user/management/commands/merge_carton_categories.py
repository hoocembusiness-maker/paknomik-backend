"""
Merge Carton compact et dérivés + Carton ondulé into Carton et dérivés.

Usage:
  python manage.py merge_carton_categories --dry-run
  python manage.py merge_carton_categories
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_carton_merge import merge_carton_categories
from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    Product,
    Supplier,
    TenderCall,
)


class Command(BaseCommand):
    help = (
        "Merge Carton compact et dérivés and Carton ondulé "
        "under Carton et dérivés."
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
            try:
                stats = merge_carton_categories(
                    Category=Category,
                    CategoryAttribute=CategoryAttribute,
                    AttributeOption=AttributeOption,
                    Product=Product,
                    Supplier=Supplier,
                    SourcingRequest=TenderCall,
                    dry_run=dry_run,
                )
            except ValueError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                return

            if dry_run:
                transaction.set_rollback(True)

        merged = Category.objects.filter(slug="carton-et-derives").first()
        sub_count = (
            Category.objects.filter(parent=merged).count() if merged else 0
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Carton categories merged: {MERGED_LABEL} "
                f"({sub_count} subcategories)"
            )
        )
        self.stdout.write(
            f"  Subcategories moved: {stats['subcategories_moved']}\n"
            f"  Attribute links copied: {stats['attribute_links_copied']}\n"
            f"  Products repointed: {stats['products_repointed']}\n"
            f"  Suppliers updated: {stats['suppliers_updated']}\n"
            f"  Sourcing requests repointed: {stats['sourcing_requests_repointed']}\n"
            f"  Corrugated parent removed: {stats['corrugated_deleted']}"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))


MERGED_LABEL = "Carton et dérivés"
