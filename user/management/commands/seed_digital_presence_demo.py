"""
DEBUG-only demo data for the digital presence dashboard.

Every row created here is flagged `is_demo=True` and displayed with a
"Données simulées" badge — it is never presented as a real measure.
    python manage.py seed_digital_presence_demo --supplier 12
    python manage.py seed_digital_presence_demo --supplier 12 --clear
"""

import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from user.digital_config import WINDOWS
from user.digital_models import DigitalConnection, DigitalPlatform
from user.digital_providers import record
from user.digital_sync import upsert_records
from user.models import Supplier

DEMO = {
    DigitalPlatform.META_FACEBOOK: ("demo-fb-page", "Page Facebook (démo)", "facebook_page"),
    DigitalPlatform.META_INSTAGRAM: ("demo-ig", "@compte_demo", "instagram_account"),
    DigitalPlatform.GOOGLE_ANALYTICS: ("properties/demo", "Site vitrine (démo)", "ga4_property"),
    DigitalPlatform.GOOGLE_SEARCH_CONSOLE: ("sc-domain:demo.example", "sc-domain:demo.example", "search_console_site"),
}


class Command(BaseCommand):
    help = "Crée des données Présence digitale SIMULÉES (DEBUG uniquement), marquées comme démo."

    def add_arguments(self, parser):
        parser.add_argument("--supplier", type=int, required=True)
        parser.add_argument("--clear", action="store_true", help="Supprimer les données de démo.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Commande réservée au développement (DEBUG=True).")
        supplier = Supplier.objects.filter(pk=options["supplier"]).first()
        if supplier is None:
            raise CommandError("Fournisseur introuvable.")

        DigitalConnection.objects.filter(supplier=supplier, is_demo=True).delete()
        if options["clear"]:
            self.stdout.write(self.style.SUCCESS("Données de démonstration supprimées."))
            return

        rng = random.Random(supplier.pk)
        end = timezone.localdate() - timedelta(days=1)
        start = end - timedelta(days=92)
        now = timezone.now()
        for platform, (external_id, name, resource_type) in DEMO.items():
            connection = DigitalConnection.objects.create(
                supplier=supplier,
                platform=platform,
                external_id=external_id,
                display_name=name,
                resource_type=resource_type,
                is_demo=True,
                last_synced_at=now,
                last_connected_at=now,
                data_start=start,
                data_end=end,
                metadata={"demo": True, "unsupportedMetrics": {"profile_views": {"code": "unsupported_metric", "checkedAt": now.isoformat()}}},
            )
            records = []
            day = start
            while day <= end:
                # Deliberate gap so the UI demonstrates missing days.
                if (end - day).days not in (12, 13):
                    for key, base in self._daily(platform):
                        records.append(record(key, round(base * rng.uniform(0.6, 1.4), 4), "count", day, day, "day"))
                day += timedelta(days=1)
            for days in WINDOWS:
                window_start = end - timedelta(days=days - 1)
                for key, base, unit in self._window(platform):
                    if platform == DigitalPlatform.META_INSTAGRAM and days > 30 and key in ("reach", "accounts_engaged"):
                        continue
                    factor = days if unit == "count" else 1
                    records.append(
                        record(key, round(base * factor * rng.uniform(0.8, 1.2), 4), unit, window_start, end, "window")
                    )
            upsert_records(connection, records)
        self.stdout.write(self.style.SUCCESS(f"Données SIMULÉES créées pour {supplier.company_name}."))

    @staticmethod
    def _daily(platform):
        return {
            DigitalPlatform.META_FACEBOOK: [("page_media_view", 420), ("page_post_engagements", 35), ("page_total_actions", 4)],
            DigitalPlatform.META_INSTAGRAM: [("reach", 260)],
            DigitalPlatform.GOOGLE_ANALYTICS: [("sessions", 80), ("activeUsers", 60)],
            DigitalPlatform.GOOGLE_SEARCH_CONSOLE: [("clicks", 18), ("impressions", 640)],
        }[platform]

    @staticmethod
    def _window(platform):
        return {
            DigitalPlatform.META_FACEBOOK: [("page_total_media_view_unique", 150, "count")],
            DigitalPlatform.META_INSTAGRAM: [
                ("reach", 180, "count"),
                ("views", 520, "count"),
                ("accounts_engaged", 22, "count"),
                ("total_interactions", 40, "count"),
            ],
            DigitalPlatform.GOOGLE_ANALYTICS: [
                ("sessions", 80, "count"),
                ("activeUsers", 45, "count"),
                ("screenPageViews", 210, "count"),
                ("engagementRate", 0.58, "ratio"),
            ],
            DigitalPlatform.GOOGLE_SEARCH_CONSOLE: [
                ("clicks", 18, "count"),
                ("impressions", 640, "count"),
                ("ctr", 0.028, "ratio"),
                ("position", 14.2, "position"),
            ],
        }[platform]
