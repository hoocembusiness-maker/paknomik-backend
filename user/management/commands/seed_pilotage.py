"""Initialize Espace de pilotage weeks, checklist and metric targets (idempotent)."""

from django.core.management.base import BaseCommand

from user.pilotage import seed_pilotage_catalog


class Command(BaseCommand):
    help = "Seed the Espace de pilotage catalog (weeks, checklist, metrics). Safe to re-run."

    def handle(self, *args, **options):
        report = seed_pilotage_catalog()
        self.stdout.write(
            self.style.SUCCESS(
                "Pilotage seed OK — "
                f"weeks+{report['weeks']}, checklist+{report['checklist_items']}, "
                f"metrics+{report['metrics']}"
            )
        )
