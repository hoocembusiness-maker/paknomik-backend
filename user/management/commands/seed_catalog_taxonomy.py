"""Load the example Famille / Type taxonomy. Safe to re-run."""

from django.core.management.base import BaseCommand

from user.catalog_seed import seed_catalog_taxonomy


class Command(BaseCommand):
    help = "Seed example product families, types, attribute schemas and alternatives."

    def handle(self, *args, **options):
        stats = seed_catalog_taxonomy()
        self.stdout.write(
            "Familles {families}, types {types}, alternatives {alternatives}.".format(**stats)
        )
