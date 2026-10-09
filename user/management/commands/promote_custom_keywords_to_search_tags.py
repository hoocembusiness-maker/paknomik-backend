"""
Django management command.
Run: python manage.py promote_custom_keywords_to_search_tags
Turns product keywords into shared search tags.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from user.models import Product
from user.search_tag_utils import promote_product_custom_keywords


class Command(BaseCommand):
    help = "Convert product custom_keywords JSON into SearchTag entries linked via M2M."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report products that would be migrated without writing changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = Product.objects.filter(
            Q(custom_keywords__isnull=False) & ~Q(custom_keywords=[])
        )
        total = qs.count()
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"{total} product(s) with legacy custom keywords.")
            )
            return

        migrated = 0
        for product in qs.iterator():
            promote_product_custom_keywords(product)
            migrated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Migrated custom keywords for {migrated} product(s) into SearchTag."
            )
        )
