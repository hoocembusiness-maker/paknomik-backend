"""
Add the "Tube en carton et mandrins" subcategory under Carton et dérivés.

Usage:
  python manage.py seed_tube_carton_subcategory --dry-run
  python manage.py seed_tube_carton_subcategory
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_carton_merge import COMPACT_SLUG, MERGED_SLUG
from user.models import Category

PARENT_SLUG = MERGED_SLUG
PARENT_SLUG_LEGACY = COMPACT_SLUG

SUBCATEGORY = {
    "external_id": "v4-sub-tubes-carton-mandrins",
    "slug": "tubes-carton-mandrins",
    "sort_order": 1029,
    "level": 2,
    "name_en": "Cardboard Tubes & Cores",
    "name_fr": "Tube en carton et mandrins",
    "plain_description": "Tubes en carton et mandrins pour emballage et industrie.",
    "is_new_in_v2": True,
}


class Command(BaseCommand):
    help = (
        "Seed Tube en carton et mandrins subcategory under Carton et dérivés."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        parent = (
            Category.objects.filter(slug=PARENT_SLUG, parent__isnull=True).first()
            or Category.objects.filter(slug=PARENT_SLUG_LEGACY, parent__isnull=True).first()
        )
        if not parent:
            self.stderr.write(
                self.style.ERROR(
                    f'Parent category "{PARENT_SLUG}" not found. Run catalog import first.'
                )
            )
            return

        with transaction.atomic():
            existing = Category.objects.filter(
                slug=SUBCATEGORY["slug"], parent=parent
            ).first() or Category.objects.filter(
                external_id=SUBCATEGORY["external_id"]
            ).first()

            if dry_run:
                if existing:
                    self.stdout.write(
                        f"Would update subcategory: {existing.name_fr} "
                        f"(parent → {parent.name_fr})"
                    )
                else:
                    self.stdout.write(
                        f"Would create subcategory « {SUBCATEGORY['name_fr']} » "
                        f"under {parent.name_fr}"
                    )
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run — no changes saved."))
                return

            subcategory, created = Category.objects.update_or_create(
                external_id=SUBCATEGORY["external_id"],
                defaults={
                    **SUBCATEGORY,
                    "parent": parent,
                },
            )

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"Subcategory « {subcategory.name_fr} » {action} under {parent.name_fr} "
                f"(id={subcategory.id}, slug={subcategory.slug})"
            )
        )
