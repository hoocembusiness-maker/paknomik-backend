"""
Add the on-demand printing service category and promotion subcategories.

Usage:
  python manage.py seed_printing_category --dry-run
  python manage.py seed_printing_category
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from user.models import Category

PARENT = {
    "external_id": "v4-print-001",
    "slug": "impression-demande",
    "sort_order": 16,
    "level": 1,
    "name_en": "On-Demand Printing",
    "name_fr": "Impression à la demande",
    "plain_description": (
        "Services d'impression à la demande pour la promotion "
        "(dépliants, affiches, tentes de table, menus, etc.)."
    ),
    "is_new_in_v2": True,
}

PROMOTION_GROUP = {
    "external_id": "v4-sub-print-promo",
    "slug": "promotion",
    "sort_order": 1600,
    "level": 2,
    "name_en": "Promotion",
    "name_fr": "Promotion",
    "plain_description": "Supports promotion imprimés à la demande.",
    "is_new_in_v2": True,
}

SUBCATEGORIES = [
    {
        "external_id": "v4-sub-print-001",
        "slug": "depliant-2-volets",
        "sort_order": 1601,
        "name_en": "2-Panel Brochure",
        "name_fr": "Dépliant à 2 volets",
    },
    {
        "external_id": "v4-sub-print-002",
        "slug": "depliant-3-volets",
        "sort_order": 1602,
        "name_en": "3-Panel Brochure",
        "name_fr": "Dépliant à 3 volets",
    },
    {
        "external_id": "v4-sub-print-003",
        "slug": "depliant-4-volets",
        "sort_order": 1603,
        "name_en": "4-Panel Brochure",
        "name_fr": "Dépliant à 4 volets",
    },
    {
        "external_id": "v4-sub-print-004",
        "slug": "poster",
        "sort_order": 1604,
        "name_en": "Poster",
        "name_fr": "Poster",
    },
    {
        "external_id": "v4-sub-print-005",
        "slug": "tente-de-table",
        "sort_order": 1605,
        "name_en": "Table Tent",
        "name_fr": "Tente de table",
    },
    {
        "external_id": "v4-sub-print-006",
        "slug": "menu-restaurant",
        "sort_order": 1606,
        "name_en": "Restaurant Menu",
        "name_fr": "Menu de restaurant",
    },
]


class Command(BaseCommand):
    help = "Seed on-demand printing category and promotion subcategories."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        with transaction.atomic():
            parent, parent_created = self._upsert_category(PARENT, parent=None, dry_run=dry_run)
            promo, promo_created = self._upsert_category(
                PROMOTION_GROUP, parent=parent, dry_run=dry_run
            )

            sub_results = []
            for row in SUBCATEGORIES:
                payload = {
                    **row,
                    "level": 3,
                    "is_new_in_v2": True,
                    "plain_description": f"Promotion — {row['name_fr']}.",
                }
                _, created = self._upsert_category(payload, parent=promo, dry_run=dry_run)
                sub_results.append((row["name_fr"], created))

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Parent: {PARENT['name_fr']} ({'created' if parent_created else 'updated'})"
            )
        )
        self.stdout.write(
            f"Group: {PROMOTION_GROUP['name_fr']} "
            f"({'created' if promo_created else 'updated'})"
        )
        for name_fr, created in sub_results:
            self.stdout.write(f"  - {name_fr} ({'created' if created else 'updated'})")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))

    def _upsert_category(self, data, parent, dry_run):
        if dry_run:
            existing = Category.objects.filter(external_id=data["external_id"]).first()
            if existing:
                return existing, False
            if parent is None:
                return Category(**data), True
            return Category(parent=parent, **data), True

        defaults = {
            "slug": data.get("slug") or slugify(data["name_en"]),
            "sort_order": data["sort_order"],
            "level": data["level"],
            "name_en": data["name_en"],
            "name_fr": data.get("name_fr", ""),
            "plain_description": data.get("plain_description", ""),
            "is_new_in_v2": data.get("is_new_in_v2", False),
            "parent": parent,
        }
        category, created = Category.objects.update_or_create(
            external_id=data["external_id"],
            defaults=defaults,
        )
        return category, created
