"""
Replace the « Taille » filter on Emballage flexible with Longueur / Largeur (cm).

Usage:
  python manage.py seed_flexpak_dimensions --dry-run
  python manage.py seed_flexpak_dimensions
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from user.models import Category, CategoryAttribute, TechnicalAttribute, Unit

PARENT_SLUG = "flexpak"
TAILLE_EXTERNAL_ID = "v4-03-L1-04"
REMOVED_MM_EXTERNAL_IDS = (
    "v4-03-L2-01",  # Largeur (mm)
)

DIMENSION_ATTRIBUTES = [
    {
        "external_id": "v4-flexpak-L1-longueur",
        "name_en": "Length",
        "name_fr": "Longueur",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "cm",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 4,
    },
    {
        "external_id": "v4-flexpak-L1-largeur",
        "name_en": "Width",
        "name_fr": "Largeur",
        "attribute_level": 1,
        "attribute_type": "number",
        "unit_symbol": "cm",
        "is_required": False,
        "is_searchable": True,
        "sort_order": 5,
    },
]

SORT_AFTER_DIMENSIONS = {
    "v4-03-L1-05": 6,  # Impression
    "v4-03-L2-02": 7,  # Hauteur (mm)
    "v4-03-L2-03": 8,  # Soufflet (mm)
    "v4-03-L2-04": 9,
    "v4-03-L2-05": 10,
    "v4-03-L2-06": 11,
}


def apply_flexpak_dimensions(*, Category, CategoryAttribute, TechnicalAttribute, Unit, dry_run=False):
    parent = Category.objects.filter(slug=PARENT_SLUG, parent__isnull=True).first()
    if not parent:
        raise ValueError(f'Parent category "{PARENT_SLUG}" not found.')

    stats = {"removed": 0, "added": 0, "reordered": 0, "mm_removed": 0}

    taille_links = CategoryAttribute.objects.filter(
        category=parent,
        attribute__external_id=TAILLE_EXTERNAL_ID,
    )
    if dry_run:
        stats["removed"] = taille_links.count()
    else:
        stats["removed"], _ = taille_links.delete()

    mm_links = CategoryAttribute.objects.filter(
        category=parent,
        attribute__external_id__in=REMOVED_MM_EXTERNAL_IDS,
    )
    if dry_run:
        stats["mm_removed"] = mm_links.count()
    else:
        deleted, _ = mm_links.delete()
        stats["mm_removed"] = deleted

    for spec in DIMENSION_ATTRIBUTES:
        stats["added"] += 1
        if dry_run:
            continue

        unit = None
        if spec.get("unit_symbol"):
            unit = Unit.objects.filter(symbol=spec["unit_symbol"]).first()
            if not unit:
                unit, _ = Unit.objects.get_or_create(
                    name=spec["unit_symbol"],
                    defaults={"symbol": spec["unit_symbol"]},
                )

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
            category=parent,
            attribute=attribute,
            defaults={
                "sort_order": spec["sort_order"],
                "is_required_for_publish": spec["is_required"],
            },
        )

    for external_id, sort_order in SORT_AFTER_DIMENSIONS.items():
        stats["reordered"] += 1
        if dry_run:
            continue
        CategoryAttribute.objects.filter(
            category=parent,
            attribute__external_id=external_id,
        ).update(sort_order=sort_order)

    return parent, stats


class Command(BaseCommand):
    help = "Replace Taille with Longueur / Largeur (cm) on Emballage flexible."

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
                parent, stats = apply_flexpak_dimensions(
                    Category=Category,
                    CategoryAttribute=CategoryAttribute,
                    TechnicalAttribute=TechnicalAttribute,
                    Unit=Unit,
                    dry_run=dry_run,
                )
            except ValueError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                return

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"{parent.name_fr}: removed {stats['removed']} Taille link(s), "
                f"removed {stats['mm_removed']} mm dimension link(s), "
                f"added {stats['added']} dimension attribute(s), "
                f"reordered {stats['reordered']} link(s)"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))
