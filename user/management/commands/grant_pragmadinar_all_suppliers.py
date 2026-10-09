"""
Crédite le Pragmadinar de bienvenue à chaque fournisseur actif (idempotent).

Run: python manage.py grant_pragmadinar_all_suppliers
"""

from django.core.management.base import BaseCommand

from user.models import Supplier
from user.pragmadinar import grant_supplier_welcome_pragmadinar, get_or_create_wallet, _welcome_grant_already_applied
from user.platform_config import supplier_welcome_pragmadinar


class Command(BaseCommand):
    help = (
        "Accorde le crédit de bienvenue Pragmadinar (100 par défaut) "
        "à tous les fournisseurs qui ne l'ont pas encore reçu."
    )

    def handle(self, *args, **options):
        amount = supplier_welcome_pragmadinar()
        credited = 0
        skipped = 0

        for supplier in Supplier.objects.filter(is_active=True).iterator():
            wallet = get_or_create_wallet(supplier)
            if _welcome_grant_already_applied(wallet):
                skipped += 1
                continue

            grant_supplier_welcome_pragmadinar(supplier)
            credited += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Terminé : {credited} fournisseur(s) crédité(s) de {amount} Pragmadinar, "
                f"{skipped} déjà traité(s)."
            )
        )
