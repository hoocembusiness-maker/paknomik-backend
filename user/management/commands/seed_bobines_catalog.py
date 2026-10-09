"""
Seed Bobines category tree and technical attributes (idempotent).

Usage:
  python manage.py seed_bobines_catalog --dry-run
  python manage.py seed_bobines_catalog
"""

from django.core.management.base import BaseCommand

from user.bobines_seed import apply_bobines_catalog


class Command(BaseCommand):
    help = "Seed Bobines parent category, six subcategories, and technical attributes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        stats = apply_bobines_catalog(dry_run=dry_run)

        if stats.errors:
            for err in stats.errors:
                self.stderr.write(self.style.ERROR(err))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Bobines catalog: parent_created={stats.parent_created}, "
                f"subcategories={stats.subcategories}, attributes={stats.attributes}, "
                f"links={stats.links}, options={stats.options}"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes saved."))
