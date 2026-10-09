"""
Create (or refresh) the beta supplier account used to test Paknomik features.

    python manage.py seed_beta_supplier
    python manage.py seed_beta_supplier --reset-password
    python manage.py seed_beta_supplier --hidden        # hide from the public marketplace

Idempotent: re-running never duplicates the account nor the test credits.
The password is generated randomly and printed once; it is never stored in the repo.
"""

import secrets
from decimal import Decimal
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user.models import PragmadinarLedger, Supplier, User
from user.pragmadinar import (
    WALLET_CATEGORIES,
    WELCOME_GRANT_IDEMPOTENCY_PREFIX,
    _welcome_grant_already_applied,
    credit_wallet,
    grant_supplier_welcome_pragmadinar,
)

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "embalium_logo.png"
BETA_PRESENTATION = (
    "Compte fournisseur bêta Paknomik, utilisé uniquement pour tester les fonctionnalités. "
    "Ne pas contacter."
)


class Command(BaseCommand):
    help = "Crée le compte fournisseur bêta « Embalium » pour tester les fonctionnalités."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Embalium")
        parser.add_argument("--email", default="embalium.beta@paknomik.test")
        parser.add_argument("--username", default="embalium")
        parser.add_argument(
            "--credit",
            type=Decimal,
            default=Decimal("200"),
            help=(
                "Crédit de test total (offert, remplace le crédit de bienvenue), "
                "réparti entre visibilité, analytics et promotion."
            ),
        )
        parser.add_argument("--reset-password", action="store_true", help="Génère un nouveau mot de passe.")
        parser.add_argument("--hidden", action="store_true", help="Masque le fournisseur de la marketplace.")

    @transaction.atomic
    def handle(self, *args, **opts):
        email = opts["email"].strip().lower()
        username = opts["username"].strip()

        clash = User.objects.filter(username=username).exclude(email=email).first()
        if clash:
            raise CommandError(f"Le nom d'utilisateur « {username} » est déjà pris par un autre compte.")

        supplier, supplier_created = Supplier.objects.get_or_create(
            email=email,
            defaults={
                "company_name": opts["name"],
                "wilaya": "algiers",
                "type": "manufacturer",
                "presentation": BETA_PRESENTATION,
            },
        )
        supplier.is_active = not opts["hidden"]
        supplier.save(update_fields=["is_active", "updated_at"])

        if not supplier.logo and LOGO_PATH.exists():
            with LOGO_PATH.open("rb") as fh:
                supplier.logo.save("embalium_logo.png", File(fh), save=True)

        user = User.objects.filter(email=email).first()
        password = None
        if user is None:
            password = secrets.token_urlsafe(12)
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                company_name=opts["name"],
                supplier=supplier,
            )
        else:
            if user.supplier_id != supplier.pk:
                user.supplier = supplier
                user.save(update_fields=["supplier"])
            if opts["reset_password"]:
                password = secrets.token_urlsafe(12)
                user.set_password(password)
                user.save(update_fields=["password"])

        credited = []
        if opts["credit"] <= 0:
            grant_supplier_welcome_pragmadinar(supplier)
        elif not _welcome_grant_already_applied(supplier):
            # Recorded under the welcome keys so the welcome grant is never added on top.
            total = opts["credit"]
            share = (total / Decimal("3")).quantize(Decimal("0.01"))
            assigned = Decimal("0")
            for index, category in enumerate(WALLET_CATEGORIES):
                portion = total - assigned if index == len(WALLET_CATEGORIES) - 1 else share
                assigned += portion
                entry = credit_wallet(
                    supplier,
                    portion,
                    reason="Crédit de test — compte bêta",
                    entry_type=PragmadinarLedger.EntryType.GRANT,
                    idempotency_key=f"{WELCOME_GRANT_IDEMPOTENCY_PREFIX}{supplier.pk}:{category}",
                    wallet_category=category,
                )
                credited.append((category, entry.amount))

        self.stdout.write(self.style.SUCCESS(
            f"Fournisseur bêta {'créé' if supplier_created else 'mis à jour'} : "
            f"{supplier.company_name} (id {supplier.pk})"
            f"{' — masqué de la marketplace' if opts['hidden'] else ''}"
        ))
        self.stdout.write(f"  Connexion : {email}  (ou identifiant « {user.username} »)")
        if password:
            self.stdout.write(self.style.WARNING(f"  Mot de passe : {password}  (affiché une seule fois)"))
        else:
            self.stdout.write("  Mot de passe inchangé (utilisez --reset-password pour en générer un nouveau).")
        for category, amount in credited:
            self.stdout.write(f"  Portefeuille {category} : crédit de test {amount} Pragmadinar")
