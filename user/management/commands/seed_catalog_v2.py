"""
Seed v2 catalog categories and technical attributes from 02_catalog_database.md.

Usage:
  python manage.py seed_catalog_v2 --dry-run
  python manage.py seed_catalog_v2
  python manage.py seed_catalog_v2 --clear
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

TECHNICAL_CATEGORIES = [
    {
        "sort_order": 1,
        "slug": "bottles-jars",
        "name_en": "Bottles & Jars",
        "plain_description": (
            "Rigid containers with necks — plastic, glass, aluminum"
        ),
        "is_new_in_v2": False,
    },
    {
        "sort_order": 2,
        "slug": "rigid-boxes-cartons",
        "name_en": "Rigid Boxes & Cartons",
        "plain_description": (
            "Flat-pack and setup boxes, folding cartons, display boxes"
        ),
        "is_new_in_v2": False,
    },
    {
        "sort_order": 3,
        "slug": "bags-pouches",
        "name_en": "Bags & Pouches",
        "plain_description": "Flexible stand-up, flat, and multi-layer bags",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 4,
        "slug": "cups-tumblers",
        "name_en": "Cups & Tumblers",
        "plain_description": "Paper, plastic, and compostable cups and lids",
        "is_new_in_v2": True,
    },
    {
        "sort_order": 5,
        "slug": "shopping-carrier-bags",
        "name_en": "Shopping & Carrier Bags",
        "plain_description": (
            "Retail bags — paper, plastic, non-woven, tote"
        ),
        "is_new_in_v2": True,
    },
    {
        "sort_order": 6,
        "slug": "wrapping-film",
        "name_en": "Wrapping & Film",
        "plain_description": (
            "Gift wrap, tissue paper, shrink film, cellophane, stretch film"
        ),
        "is_new_in_v2": True,
    },
    {
        "sort_order": 7,
        "slug": "labels-stickers",
        "name_en": "Labels & Stickers",
        "plain_description": "Self-adhesive, shrink sleeve, in-mold, RFID",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 8,
        "slug": "caps-closures",
        "name_en": "Caps & Closures",
        "plain_description": "Screw caps, pumps, flip-tops, sprays, droppers",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 9,
        "slug": "tubes",
        "name_en": "Tubes",
        "plain_description": "Laminate, aluminum, plastic squeeze tubes",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 10,
        "slug": "trays-containers",
        "name_en": "Trays & Containers",
        "plain_description": (
            "Open trays, clamshells, portion cups, takeaway containers"
        ),
        "is_new_in_v2": True,
    },
    {
        "sort_order": 11,
        "slug": "drums-industrial",
        "name_en": "Drums & Industrial",
        "plain_description": "Steel/HDPE drums, IBCs, jerry cans, pails",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 12,
        "slug": "foam-protective",
        "name_en": "Foam & Protective",
        "plain_description": "EPS, EPE, polyurethane inserts and sheets",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 13,
        "slug": "paper-corrugated",
        "name_en": "Paper & Corrugated",
        "plain_description": "Corrugated boxes, kraft paper, honeycomb board",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 14,
        "slug": "cosmetic-components",
        "name_en": "Cosmetic Components",
        "plain_description": "Compacts, palettes, airless pumps, mascara tubes",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 15,
        "slug": "pharmaceutical-primary",
        "name_en": "Pharmaceutical Primary",
        "plain_description": "Blisters, vials, ampoules, sachets, capsule strips",
        "is_new_in_v2": False,
    },
    {
        "sort_order": 16,
        "slug": "specialty-other",
        "name_en": "Specialty & Other",
        "plain_description": (
            "Anything that doesn't fit above — prevents dead ends"
        ),
        "is_new_in_v2": False,
    },
]

# Level 1 = choice dropdowns; Level 2 = number/text/boolean specs.
CATEGORY_ATTRIBUTES = {
    "shopping-carrier-bags": {
        "level_1": [
            {
                "name": "Material",
                "options": [
                    "Kraft paper",
                    "Coated paper",
                    "HDPE plastic",
                    "Non-woven PP",
                    "Cotton",
                    "Jute",
                ],
            },
            {
                "name": "Handle type",
                "options": [
                    "Twisted",
                    "Flat ribbon",
                    "Die-cut",
                    "No handle",
                    "Rope",
                ],
            },
            {
                "name": "Size",
                "options": ["XS (< 20cm)", "S", "M", "L", "XL", "Custom"],
            },
            {
                "name": "Print",
                "options": [
                    "Unprinted",
                    "1–2 colors",
                    "Full CMYK",
                    "Custom branding",
                ],
            },
        ],
        "level_2": [
            {"name": "Width", "type": "number", "unit": "mm"},
            {"name": "Height", "type": "number", "unit": "mm"},
            {"name": "Gusset", "type": "number", "unit": "mm"},
            {"name": "Weight capacity", "type": "number", "unit": "kg"},
            {"name": "GSM (paper bags)", "type": "number", "unit": None},
            {"name": "Recyclable / Biodegradable", "type": "boolean", "unit": None},
        ],
    },
    "wrapping-film": {
        "level_1": [
            {
                "name": "Type",
                "options": [
                    "Gift wrap paper",
                    "Tissue paper",
                    "Shrink film",
                    "Stretch film",
                    "Cellophane",
                    "Foil wrap",
                ],
            },
            {
                "name": "Material",
                "options": ["Paper", "OPP", "PVC", "PE", "Aluminum foil"],
            },
            {
                "name": "Format",
                "options": ["Roll", "Sheet", "Pre-cut"],
            },
            {
                "name": "Print",
                "options": ["Plain", "Solid color", "Pattern", "Custom print"],
            },
        ],
        "level_2": [
            {"name": "Roll width", "type": "number", "unit": "mm"},
            {"name": "Roll length", "type": "number", "unit": "m"},
            {"name": "Thickness", "type": "number", "unit": "microns"},
            {"name": "Shrink ratio (shrink film)", "type": "number", "unit": "%"},
            {"name": "Food-contact safe", "type": "boolean", "unit": None},
        ],
    },
    "cups-tumblers": {
        "level_1": [
            {
                "name": "Material",
                "options": [
                    "Single-wall paper",
                    "Double-wall paper",
                    "Plastic PP",
                    "PET cold cup",
                    "Compostable PLA",
                ],
            },
            {
                "name": "Type",
                "options": [
                    "Hot cup",
                    "Cold cup",
                    "Tumbler",
                    "Espresso cup",
                    "Soup container",
                ],
            },
            {
                "name": "Volume",
                "options": [
                    "4 oz",
                    "6 oz",
                    "8 oz",
                    "12 oz",
                    "16 oz",
                    "20 oz",
                    "32 oz",
                ],
            },
            {
                "name": "Lid compatible",
                "options": ["Yes", "No", "Included"],
            },
        ],
        "level_2": [
            {"name": "Exact volume", "type": "number", "unit": "ml"},
            {"name": "Top diameter", "type": "number", "unit": "mm"},
            {"name": "Bottom diameter", "type": "number", "unit": "mm"},
            {"name": "Height", "type": "number", "unit": "mm"},
            {
                "name": "Insulation",
                "type": "choice",
                "options": ["Single-wall", "Double-wall", "Ripple", "Sleeve"],
            },
            {
                "name": "Certification",
                "type": "choice",
                "options": ["FSC", "FDA food-safe", "Compostable certified"],
            },
        ],
    },
    "trays-containers": {
        "level_1": [
            {
                "name": "Type",
                "options": [
                    "Pizza box",
                    "Burger box",
                    "Sandwich bag",
                    "Hot dog tray",
                    "Takeaway container",
                    "Clamshell",
                    "Portion cup",
                    "Sauce cup",
                ],
            },
            {
                "name": "Material",
                "options": [
                    "Corrugated paper",
                    "Kraft board",
                    "Aluminum foil",
                    "PP",
                    "PET",
                    "Sugarcane bagasse",
                    "Compostable",
                ],
            },
            {
                "name": "Grease-resistant",
                "options": ["Yes", "No"],
            },
            {
                "name": "Temperature range",
                "options": [
                    "Cold only",
                    "Hot up to 100°C",
                    "Oven-safe",
                    "Microwave-safe",
                ],
            },
        ],
        "level_2": [
            {
                "name": "Dimensions (L × W × H)",
                "type": "text",
                "unit": "mm",
            },
            {"name": "Capacity", "type": "number", "unit": "ml"},
            {"name": "Stackable", "type": "boolean", "unit": None},
            {"name": "Vented (pizza boxes)", "type": "boolean", "unit": None},
            {
                "name": "Certification",
                "type": "choice",
                "options": ["Food-safe", "Halal", "Compostable", "HACCP"],
            },
            {"name": "Print area", "type": "number", "unit": "cm²"},
        ],
    },
}


class Command(BaseCommand):
    help = "Seed v2 flat catalog categories and technical attributes"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report without writing to the database",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing v2 category/attribute data before seeding",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        clear = options["clear"]

        with transaction.atomic():
            if clear and not dry_run:
                self._clear_v2_catalog()

            category_count = self._seed_categories(dry_run)
            attr_stats = self._seed_attributes(dry_run)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS("Seed summary:"))
        self.stdout.write(f"  categories: {category_count}")
        self.stdout.write(f"  attributes: {attr_stats['attributes']}")
        self.stdout.write(f"  category links: {attr_stats['links']}")
        self.stdout.write(f"  options: {attr_stats['options']}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes saved"))

    def _clear_v2_catalog(self):
        AttributeOption.objects.all().delete()
        CategoryAttribute.objects.all().delete()
        TechnicalAttribute.objects.filter(
            external_id__startswith="v2-"
        ).delete()
        Category.objects.filter(external_id__startswith="v2-").delete()
        self.stdout.write(self.style.WARNING("Cleared v2 catalog data"))

    def _seed_categories(self, dry_run):
        count = 0
        for data in TECHNICAL_CATEGORIES:
            external_id = f"v2-{data['sort_order']:02d}"
            defaults = {
                "slug": data["slug"],
                "sort_order": data["sort_order"],
                "name_en": data["name_en"],
                "plain_description": data["plain_description"],
                "is_new_in_v2": data["is_new_in_v2"],
                "level": 1,
                "parent": None,
            }
            if not dry_run:
                Category.objects.update_or_create(
                    external_id=external_id,
                    defaults=defaults,
                )
            count += 1
        return count

    def _resolve_unit(self, symbol):
        if not symbol:
            return None
        unit, _ = Unit.objects.get_or_create(
            name=symbol,
            defaults={"symbol": symbol},
        )
        return unit

    def _attribute_external_id(self, category_order, attr_order, level):
        return f"v2-{category_order:02d}-L{level}-{attr_order:02d}"

    def _seed_attributes(self, dry_run):
        stats = {"attributes": 0, "links": 0, "options": 0}

        for category_slug, spec in CATEGORY_ATTRIBUTES.items():
            category = None
            category_order = None
            if not dry_run:
                category = Category.objects.filter(slug=category_slug).first()
                if not category:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Category not found for slug: {category_slug}"
                        )
                    )
                    continue
                category_order = category.sort_order

            for sort_idx, attr_data in enumerate(spec.get("level_1", []), start=1):
                stats["attributes"] += 1
                stats["links"] += 1
                option_count = len(attr_data.get("options", []))
                stats["options"] += option_count

                if dry_run:
                    continue

                external_id = self._attribute_external_id(
                    category_order, sort_idx, 1
                )
                attribute, _ = TechnicalAttribute.objects.update_or_create(
                    external_id=external_id,
                    defaults={
                        "name_en": attr_data["name"],
                        "attribute_level": 1,
                        "attribute_type": "choice",
                        "is_required": True,
                        "is_searchable": True,
                    },
                )
                link, _ = CategoryAttribute.objects.update_or_create(
                    category=category,
                    attribute=attribute,
                    defaults={
                        "sort_order": sort_idx,
                        "is_required_for_publish": True,
                    },
                )
                for opt_idx, option in enumerate(attr_data.get("options", []), start=1):
                    AttributeOption.objects.update_or_create(
                        category_attribute=link,
                        value_en=option,
                        defaults={"sort_order": opt_idx},
                    )

            level_1_count = len(spec.get("level_1", []))
            for sort_idx, attr_data in enumerate(spec.get("level_2", []), start=1):
                stats["attributes"] += 1
                stats["links"] += 1

                attr_type = attr_data.get("type", "text")
                options = attr_data.get("options", [])

                if dry_run:
                    stats["options"] += len(options)
                    continue

                external_id = self._attribute_external_id(
                    category_order, sort_idx, 2
                )
                unit = self._resolve_unit(attr_data.get("unit"))
                attribute, _ = TechnicalAttribute.objects.update_or_create(
                    external_id=external_id,
                    defaults={
                        "name_en": attr_data["name"],
                        "attribute_level": 2,
                        "attribute_type": attr_type,
                        "unit": unit,
                        "is_required": False,
                        "is_searchable": attr_type == "choice",
                    },
                )
                link, _ = CategoryAttribute.objects.update_or_create(
                    category=category,
                    attribute=attribute,
                    defaults={
                        "sort_order": level_1_count + sort_idx,
                        "is_required_for_publish": False,
                    },
                )
                for opt_idx, option in enumerate(options, start=1):
                    AttributeOption.objects.update_or_create(
                        category_attribute=link,
                        value_en=option,
                        defaults={"sort_order": opt_idx},
                    )
                    stats["options"] += 1

        return stats
