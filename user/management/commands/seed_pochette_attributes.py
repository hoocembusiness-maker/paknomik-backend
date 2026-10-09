"""
Seed technical attributes for the Pochettes subcategory (flexible packaging).

Usage:
  python manage.py seed_pochette_attributes --dry-run
  python manage.py seed_pochette_attributes
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.catalog_carton_merge import COMPACT_SLUG, MERGED_SLUG
from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    TechnicalAttribute,
    Unit,
)

CARTON_PARENT_SLUG = MERGED_SLUG
CARTON_PARENT_SLUG_LEGACY = COMPACT_SLUG
POUCHETTE_SLUG = "pochettes"

ATTRIBUTES = [
    {
        "external_id": "v4-pochette-L1-01",
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
        "external_id": "v4-pochette-L1-02",
        "name_en": "Width",
        "name_fr": "Largeur",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "cm",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 2,
    },
    {
        "external_id": "v4-pochette-L1-03",
        "name_en": "Grammage",
        "name_fr": "Grammage",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "g/m²",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 3,
    },
    {
        "external_id": "v4-pochette-L1-04",
        "name_en": "Food-contact packaging",
        "name_fr": "Emballage alimentaire",
        "attribute_level": 1,
        "attribute_type": "boolean",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": True,
        "sort_order": 4,
    },
    {
        "external_id": "v4-pochette-L1-05",
        "name_en": "Custom printing",
        "name_fr": "Impression personnalisée",
        "attribute_level": 1,
        "attribute_type": "boolean",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": True,
        "sort_order": 5,
    },
    {
        "external_id": "v4-pochette-L1-06",
        "name_en": "Finish type",
        "name_fr": "Type de finition",
        "attribute_level": 1,
        "attribute_type": "choice",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": True,
        "sort_order": 6,
        "options": [
            ("Mat", "Mat"),
            ("Glacé", "Glacé"),
            ("Pelliculé", "Pelliculé"),
        ],
    },
    {
        "external_id": "v4-pochette-L1-07",
        "name_en": "Shape",
        "name_fr": "Forme",
        "attribute_level": 1,
        "attribute_type": "text",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": False,
        "sort_order": 7,
    },
    {
        "external_id": "v4-pochette-L2-01",
        "name_en": "Max capacity",
        "name_fr": "Contenance max",
        "attribute_level": 2,
        "attribute_type": "number",
        "unit_symbol": "g",
        "is_required": False,
        "is_searchable": False,
        "sort_order": 8,
    },
]


class Command(BaseCommand):
    help = "Seed technical attributes for the Pochettes subcategory."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        with transaction.atomic():
            parent = (
                Category.objects.filter(
                    slug=CARTON_PARENT_SLUG, parent__isnull=True
                ).first()
                or Category.objects.filter(
                    slug=CARTON_PARENT_SLUG_LEGACY, parent__isnull=True
                ).first()
            )
            if not parent:
                self.stderr.write(
                    self.style.ERROR(
                        f"Parent category '{CARTON_PARENT_SLUG}' not found. "
                        "Run catalog import first."
                    )
                )
                return

            subcategory = self._resolve_pochette_subcategory(parent, dry_run)
            if subcategory is None:
                return

            stats = self._seed_attributes(subcategory, dry_run)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Pochettes ({subcategory.slug}): "
                f"{stats['attributes']} attributes, "
                f"{stats['links']} links, "
                f"{stats['options']} options"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))

    def _resolve_pochette_subcategory(self, parent, dry_run):
        subcategory = (
            Category.objects.filter(slug=POUCHETTE_SLUG, parent=parent).first()
            or Category.objects.filter(name_fr__iexact="Pochettes", parent=parent).first()
            or Category.objects.filter(slug="compacts", name_fr__icontains="Pochettes").first()
        )

        if subcategory:
            if dry_run:
                self.stdout.write(
                    f"Would update subcategory: {subcategory.name_fr} "
                    f"(slug → {POUCHETTE_SLUG}, parent → {parent.name_fr})"
                )
                return subcategory

            subcategory.parent = parent
            subcategory.slug = POUCHETTE_SLUG
            subcategory.name_fr = "Pochettes"
            subcategory.name_en = subcategory.name_en or "Pouches"
            subcategory.level = 2
            subcategory.save(
                update_fields=[
                    "parent",
                    "slug",
                    "name_fr",
                    "name_en",
                    "level",
                ]
            )
            return subcategory

        if dry_run:
            self.stdout.write(
                f"Would create subcategory Pochettes under {parent.name_fr}"
            )
            return Category(name_fr="Pochettes", slug=POUCHETTE_SLUG)

        subcategory, _ = Category.objects.update_or_create(
            external_id="v4-sub-pochettes",
            defaults={
                "parent": parent,
                "slug": POUCHETTE_SLUG,
                "sort_order": 353,
                "level": 2,
                "name_en": "Pouches",
                "name_fr": "Pochettes",
                "plain_description": "Pochettes et sachets flexibles.",
                "is_new_in_v2": True,
            },
        )
        return subcategory

    def _resolve_unit(self, symbol):
        if not symbol:
            return None
        unit, _ = Unit.objects.get_or_create(
            name=symbol,
            defaults={"symbol": symbol},
        )
        return unit

    def _seed_attributes(self, subcategory, dry_run):
        stats = {"attributes": 0, "links": 0, "options": 0}

        for spec in ATTRIBUTES:
            stats["attributes"] += 1
            stats["links"] += 1
            stats["options"] += len(spec.get("options", []))

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
            link, _ = CategoryAttribute.objects.update_or_create(
                category=subcategory,
                attribute=attribute,
                defaults={
                    "sort_order": spec["sort_order"],
                    "is_required_for_publish": spec["is_required"],
                },
            )
            for opt_idx, (value_en, value_fr) in enumerate(
                spec.get("options", []), start=1
            ):
                AttributeOption.objects.update_or_create(
                    category_attribute=link,
                    value_en=value_en,
                    defaults={
                        "value_fr": value_fr,
                        "sort_order": opt_idx,
                    },
                )

        return stats
