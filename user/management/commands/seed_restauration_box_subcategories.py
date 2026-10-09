"""
Add Boîte à pizza and Boîte pour tacos subcategories under Restauration & Café.

Usage:
  python manage.py seed_restauration_box_subcategories --dry-run
  python manage.py seed_restauration_box_subcategories
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.models import Category

PARENT_SLUG = "restauration-cafe"

SUBCATEGORIES = [
    {
        "external_id": "v4-sub-boite-a-pizza",
        "slug": "boite-a-pizza",
        "sort_order": 1402,
        "level": 2,
        "name_en": "Pizza box",
        "name_fr": "Boîte à pizza",
        "plain_description": "Boîtes à pizza pour restauration et livraison.",
        "is_new_in_v2": True,
    },
    {
        "external_id": "v4-sub-boite-pour-tacos",
        "slug": "boite-pour-tacos",
        "sort_order": 1403,
        "level": 2,
        "name_en": "Taco box",
        "name_fr": "Boîte pour tacos",
        "plain_description": "Boîtes et emballages pour tacos et street food.",
        "is_new_in_v2": True,
    },
]


class Command(BaseCommand):
    help = (
        "Seed Boîte à pizza and Boîte pour tacos subcategories under Restauration & Café."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        parent = Category.objects.filter(slug=PARENT_SLUG, parent__isnull=True).first()
        if not parent:
            self.stderr.write(
                self.style.ERROR(
                    f'Parent category "{PARENT_SLUG}" not found. Run catalog import first.'
                )
            )
            return

        with transaction.atomic():
            for row in SUBCATEGORIES:
                if dry_run:
                    existing = Category.objects.filter(
                        slug=row["slug"], parent=parent
                    ).first()
                    if existing:
                        self.stdout.write(
                            f"Would update « {row['name_fr']} » under {parent.name_fr}"
                        )
                    else:
                        self.stdout.write(
                            f"Would create « {row['name_fr']} » under {parent.name_fr}"
                        )
                    continue

                subcategory, created = Category.objects.update_or_create(
                    external_id=row["external_id"],
                    defaults={**row, "parent": parent},
                )
                action = "created" if created else "updated"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"« {subcategory.name_fr} » {action} under {parent.name_fr} "
                        f"(id={subcategory.id}, slug={subcategory.slug})"
                    )
                )

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run — no changes saved."))
