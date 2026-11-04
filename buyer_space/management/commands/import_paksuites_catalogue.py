from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from buyer_space.catalogue_import import import_catalogue

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "PakSuites_catalogue_initial.xlsx"


class Command(BaseCommand):
    help = "Importe ou met à jour le catalogue PakSuites depuis le classeur Excel."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(DEFAULT_PATH))

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"Classeur introuvable : {path}")
        report = import_catalogue(path)
        self.stdout.write(report.summary())
        if report.errors:
            raise CommandError("Import terminé avec des erreurs.")
