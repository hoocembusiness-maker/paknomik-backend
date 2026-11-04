from django.core.management.base import BaseCommand

from buyer_space.gemini_expansion import sync_gemini_expansion


class Command(BaseCommand):
    help = "Importe les kits + produits proposés par Gemini et relie les recommandations."

    def handle(self, *args, **options):
        report = sync_gemini_expansion()
        self.stdout.write(
            "Kits créés: {kits_created} · mis à jour: {kits_updated} · "
            "lignes: {lines} · produits créés: {products_created} · "
            "liés: {recommended_linked}".format(**report)
        )
