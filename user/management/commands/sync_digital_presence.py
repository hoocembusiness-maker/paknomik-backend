"""
Daily synchronization of digital presence metrics (Meta + Google).

Schedule it once a day (cron / Windows Task Scheduler), e.g.:
    python manage.py sync_digital_presence
Connections synced less than 20 h ago are skipped unless --force is given.
"""

from django.core.management.base import BaseCommand

from user.digital_models import DigitalConnection
from user.digital_sync import sync_all, sync_connection


class Command(BaseCommand):
    help = "Synchronise les statistiques Présence digitale (Facebook, Instagram, GA4, Search Console)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Ignorer le délai minimal de 20 h.")
        parser.add_argument("--connection", type=int, help="Synchroniser une seule connexion.")

    def handle(self, *args, **options):
        if options.get("connection"):
            connection = DigitalConnection.objects.select_related("authorization").get(
                pk=options["connection"]
            )
            outcome = sync_connection(connection)
            self.stdout.write(f"Connexion {connection.pk} : {outcome['status']}")
            return
        summary = sync_all(force=options["force"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Synchronisation terminée — ok: {summary.get('ok', 0)}, "
                f"erreurs: {summary.get('error', 0)}, ignorées: {summary.get('skipped', 0)}"
            )
        )
