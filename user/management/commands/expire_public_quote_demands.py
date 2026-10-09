from django.core.management.base import BaseCommand

from user.quote_market import expire_stale_public_market_listings


class Command(BaseCommand):
    help = (
        "Retire du marché les demandes publiques ouvertes depuis plus de 7 jours "
        "et les place dans la file admin Paknomik."
    )

    def handle(self, *args, **options):
        count = expire_stale_public_market_listings()
        self.stdout.write(
            self.style.SUCCESS(f"{count} demande(s) publique(s) escaladée(s).")
        )
