"""
Add the "Take away (boîte alimentaire)" subcategory under Restauration & Café
with capacity (litres) and aluminum sublayer attributes.

Usage:
  python manage.py seed_takeaway_food_box --dry-run
  python manage.py seed_takeaway_food_box
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.models import (
    Category,
    CategoryAttribute,
    TechnicalAttribute,
    Unit,
)

PARENT_SLUG = "restauration-cafe"

SUBCATEGORY = {
    "external_id": "v4-sub-takeaway-boite-alimentaire",
    "slug": "takeaway-boite-alimentaire",
    "sort_order": 1401,
    "level": 2,
    "name_en": "Take away (food box)",
    "name_fr": "Take away (boîte alimentaire)",
    "plain_description": (
        "Boîtes alimentaires take away pour restauration et cafés."
    ),
    "is_new_in_v2": True,
}

ATTRIBUTES = [
    {
        "external_id": "v4-takeaway-L1-01",
        "name_en": "Capacity",
        "name_fr": "Contenance",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "L",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 1,
    },
    {
        "external_id": "v4-takeaway-L1-02",
        "name_en": "Aluminum sublayer",
        "name_fr": "Sous-couche aluminium",
        "attribute_level": 1,
        "attribute_type": "boolean",
        "unit_symbol": None,
        "is_required": False,
        "is_searchable": True,
        "sort_order": 2,
    },
]


class Command(BaseCommand):
    help = (
        "Seed Take away (boîte alimentaire) subcategory under Restauration & Café."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        parent = Category.objects.filter(
            slug=PARENT_SLUG, parent__isnull=True
        ).first()
        if not parent:
            self.stderr.write(
                self.style.ERROR(
                    f'Parent category "{PARENT_SLUG}" not found. '
                    "Run catalog import first."
                )
            )
            return

        with transaction.atomic():
            subcategory = self._seed_subcategory(parent, dry_run)
            if subcategory is None:
                return

            stats = self._seed_attributes(subcategory, dry_run)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"« {subcategory.name_fr} » under {parent.name_fr}: "
                f"{stats['attributes']} attributes, {stats['links']} links"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))

    def _seed_subcategory(self, parent, dry_run):
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
                return existing
            self.stdout.write(
                f"Would create subcategory « {SUBCATEGORY['name_fr']} » "
                f"under {parent.name_fr}"
            )
            return Category(name_fr=SUBCATEGORY["name_fr"])

        subcategory, created = Category.objects.update_or_create(
            external_id=SUBCATEGORY["external_id"],
            defaults={
                **SUBCATEGORY,
                "parent": parent,
            },
        )
        action = "created" if created else "updated"
        self.stdout.write(
            f"Subcategory « {subcategory.name_fr} » {action} "
            f"(id={subcategory.id}, slug={subcategory.slug})"
        )
        return subcategory

    def _resolve_unit(self, symbol):
        if not symbol:
            return None
        unit = Unit.objects.filter(symbol=symbol).first()
        if unit:
            return unit
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
                self.stdout.write(
                    f"  Would link attribute « {spec['name_fr']} » "
                    f"(L{spec['attribute_level']}, {spec['attribute_type']})"
                )
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
