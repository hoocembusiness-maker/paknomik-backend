"""
Ensure every active supplier has at least 100 Pragmadinar (welcome grant + top-up).

Run: python manage.py ensure_pragmadinar_100_all_suppliers
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from user.models import PragmadinarLedger, Supplier
from user.pragmadinar import (
    grant_supplier_welcome_pragmadinar,
    get_or_create_wallet,
    normalize_legacy_welcome_grant,
    credit_wallet,
)
from user.platform_config import supplier_welcome_pragmadinar


class Command(BaseCommand):
    help = "Grant welcome Pragmadinar and top up active suppliers to 100 Pragmadinar minimum."

    @transaction.atomic
    def handle(self, *args, **options):
        target = supplier_welcome_pragmadinar()
        if target <= 0:
            self.stdout.write(self.style.WARNING("Welcome amount is 0 — nothing to do."))
            return

        granted = 0
        topped_up = 0
        already_ok = 0

        for supplier in Supplier.objects.filter(is_active=True).iterator():
            normalize_legacy_welcome_grant(supplier)
            if grant_supplier_welcome_pragmadinar(supplier):
                granted += 1

            wallet = get_or_create_wallet(supplier)
            deficit = target - wallet.balance
            if deficit > 0:
                credit_wallet(
                    supplier,
                    deficit,
                    reason="Complément crédit bienvenue Pragmadinar",
                    entry_type=PragmadinarLedger.EntryType.GRANT,
                    idempotency_key=f"bootstrap:topup100:v1:{supplier.pk}",
                )
                topped_up += 1
            else:
                already_ok += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Terminé : {granted} bienvenue(s), {topped_up} complément(s), "
                f"{already_ok} deja a {target} Pragmadinar ou plus."
            )
        )
