"""Grant the ULTRA_ADMIN role to one account, securely.

The ultra-admin is never identified by a display name: this command resolves
the account by its internal id or by its unique email, and the role is stored
as data (a RoleGrant row) verified on every backend request.

Usage:
    python manage.py assign_ultra_admin --user-id 1
    python manage.py assign_ultra_admin --email owner@example.com
    python manage.py assign_ultra_admin            # reads PAKNOMIK_ULTRA_ADMIN_EMAIL
    python manage.py assign_ultra_admin --revoke --user-id 4
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user import rbac

User = get_user_model()

ENV_ID = "PAKNOMIK_ULTRA_ADMIN_ID"
ENV_EMAIL = "PAKNOMIK_ULTRA_ADMIN_EMAIL"


class Command(BaseCommand):
    help = "Attribue (ou révoque) le rôle ULTRA_ADMIN de manière contrôlée."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None)
        parser.add_argument("--email", type=str, default=None)
        parser.add_argument("--revoke", action="store_true")
        parser.add_argument(
            "--reason",
            type=str,
            default="Attribution initiale par commande d'administration.",
        )

    def _resolve(self, options):
        user_id = options.get("user_id") or os.environ.get(ENV_ID)
        if user_id:
            user = User.objects.filter(pk=int(user_id)).first()
            if user is None:
                raise CommandError(f"Aucun compte avec l'id {user_id}.")
            return user

        email = options.get("email") or os.environ.get(ENV_EMAIL)
        if email:
            matches = list(User.objects.filter(email__iexact=email.strip())[:2])
            if not matches:
                raise CommandError(f"Aucun compte avec l'email {email}.")
            if len(matches) > 1:
                raise CommandError(
                    "Plusieurs comptes partagent cet email : utilisez --user-id."
                )
            return matches[0]

        raise CommandError(
            "Indiquez --user-id ou --email, ou définissez "
            f"{ENV_ID} / {ENV_EMAIL} dans l'environnement."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        rbac.seed_roles()
        user = self._resolve(options)

        if options["revoke"]:
            count = rbac.revoke_role(user, rbac.ULTRA_ADMIN)
            rbac.log_audit(
                None,
                "roles.revoke",
                entity_type="User",
                entity_id=user.pk,
                old_value={"role": rbac.ULTRA_ADMIN},
                reason="Révocation via commande d'administration.",
            )
            self.stdout.write(
                self.style.WARNING(
                    f"ULTRA_ADMIN révoqué pour le compte #{user.pk} ({count} attribution(s))."
                )
            )
            return

        from user.models import PlatformRole, RoleGrant

        role = PlatformRole.objects.get(code=rbac.ULTRA_ADMIN)
        grant, created = RoleGrant.objects.get_or_create(
            user=user,
            role=role,
            revoked_at=None,
            defaults={"reason": options["reason"]},
        )
        # Django admin access is separate from the CRM permissions.
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])

        rbac.log_audit(
            None,
            "roles.grant",
            entity_type="User",
            entity_id=user.pk,
            new_value={"role": rbac.ULTRA_ADMIN, "grantId": grant.pk},
            reason=options["reason"],
        )
        state = "attribué" if created else "déjà actif"
        self.stdout.write(
            self.style.SUCCESS(
                f"ULTRA_ADMIN {state} pour le compte #{user.pk} ({user.email})."
            )
        )
