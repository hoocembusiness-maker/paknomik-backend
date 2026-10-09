"""
Add Film étirable and Fardelage subcategories under Films.

Usage:
  python manage.py seed_films_subcategories --dry-run
  python manage.py seed_films_subcategories
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.models import Category

PARENT_SLUG = "films"

SUBCATEGORIES = [
    {
        "external_id": "v4-sub-film-etirable",
        "slug": "film-etirable",
        "sort_order": 1074,
        "level": 2,
        "name_en": "Stretch film",
        "name_fr": "Film étirable",
        "plain_description": "Films étirables pour palettisation et emballage.",
        "is_new_in_v2": True,
    },
    {
        "external_id": "v4-sub-fardelage",
        "slug": "fardelage",
        "sort_order": 1075,
        "level": 2,
        "name_en": "Strapping",
        "name_fr": "Fardelage",
        "plain_description": "Bandes et systèmes de fardelage / cerclage.",
        "is_new_in_v2": True,
    },
]


class Command(BaseCommand):
    help = "Seed Film étirable and Fardelage subcategories under Films."

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
