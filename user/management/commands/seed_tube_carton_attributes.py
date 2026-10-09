"""
Seed technical attributes for the Tube en carton et mandrins subcategory.

Usage:
  python manage.py seed_tube_carton_attributes --dry-run
  python manage.py seed_tube_carton_attributes
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    TechnicalAttribute,
    Unit,
)

SUBCATEGORY_SLUG = "tubes-carton-mandrins"

ATTRIBUTES = [
    {
        "external_id": "v4-tube-L1-01",
        "name_en": "Length",
        "name_fr": "Longueur",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "cm",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 1,
    },
    {
        "external_id": "v4-tube-L1-02",
        "name_en": "Internal diameter",
        "name_fr": "Diamètre interne",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "mm",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 2,
    },
    {
        "external_id": "v4-tube-L1-03",
        "name_en": "External diameter",
        "name_fr": "Diamètre externe",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "mm",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 3,
    },
    {
        "external_id": "v4-tube-L1-04",
        "name_en": "Grammage",
        "name_fr": "Grammage",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "g/m²",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 4,
    },
    {
        "external_id": "v4-tube-L1-05",
        "name_en": "Composite (plastic / aluminum)",
        "name_fr": "Composite (plastique / alu)",
        "attribute_level": 1,
        "attribute_type": "boolean",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": True,
        "sort_order": 5,
    },
]


class Command(BaseCommand):
    help = "Seed technical attributes for Tube en carton et mandrins."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        subcategory = Category.objects.filter(
            slug=SUBCATEGORY_SLUG, parent__isnull=False
        ).first()
        if not subcategory:
            self.stderr.write(
                self.style.ERROR(
                    f'Subcategory "{SUBCATEGORY_SLUG}" not found. '
                    "Run seed_tube_carton_subcategory first."
                )
            )
            return

        with transaction.atomic():
            stats = self._seed_attributes(subcategory, dry_run)
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"{subcategory.name_fr} ({subcategory.slug}): "
                f"{stats['attributes']} attributes, "
                f"{stats['links']} links"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))

    def _resolve_unit(self, symbol):
        if not symbol:
            return None
        unit, _ = Unit.objects.get_or_create(
            name=symbol,
            defaults={"symbol": symbol},
        )
        return unit

    def _seed_attributes(self, subcategory, dry_run):
        stats = {"attributes": 0, "links": 0}

        for spec in ATTRIBUTES:
            stats["attributes"] += 1
            stats["links"] += 1

            if dry_run:
                continue

            unit = self._resolve_unit(spec.get("unit_symbol"))
            attribute, _ = TechnicalAttribute.objects.update_or_create(
                external_id=spec["external_id"],
                defaults={
                    "name_en": spec["name_en"],
                    "name_fr": spec["name_fr"],
                    "attribute_level": spec["attribute_level"],
                    "attribute_type": spec["attribute_type"],
                    "unit": unit,
                    "is_required": spec["is_required"],
                    "is_searchable": spec["is_searchable"],
                },
            )
            CategoryAttribute.objects.update_or_create(
                category=subcategory,
                attribute=attribute,
                defaults={
                    "sort_order": spec["sort_order"],
                    "is_required_for_publish": spec["is_required"],
                },
            )

        return stats
