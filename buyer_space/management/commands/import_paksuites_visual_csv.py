from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from buyer_space.visual_csv_import import import_visual_csvs

DOCS = Path(__file__).resolve().parents[3] / "docs"
DEFAULT_OVERVIEW = DOCS / "paksuites_overview_prompts.csv"
DEFAULT_COMPOSANTS = DOCS / "paksuites_composants_a_produire.csv"


class Command(BaseCommand):
    help = (
        "Importe les prompts visuels PakSuites depuis docs/*.csv "
        "(overview + composants à produire) dans SuiteImage."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--overview",
            default=str(DEFAULT_OVERVIEW),
            help="Chemin vers paksuites_overview_prompts.csv",
        )
        parser.add_argument(
            "--composants",
            default=str(DEFAULT_COMPOSANTS),
            help="Chemin vers paksuites_composants_a_produire.csv",
        )
        parser.add_argument(
            "--skip-composants",
            action="store_true",
            help="N'importer que les overview",
        )

    def handle(self, *args, **options):
        overview = Path(options["overview"])
        composants = None if options["skip_composants"] else Path(options["composants"])
        report = import_visual_csvs(overview, composants)
        self.stdout.write(report.summary())
        if report.errors:
            raise CommandError("Import terminé avec des erreurs.")
