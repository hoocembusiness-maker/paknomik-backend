"""
Remove test/development analytics events (Event rows only) before a launch.

Dry run by default: prints what would be deleted. Add --yes to delete.
Several filters are combined with OR (an event matching any of them is removed).
Business data (orders, quotes, messages, favorites, Pragmadinar ledger) is never touched.

Examples:
    python manage.py purge_analytics_events --local --test-accounts
    python manage.py purge_analytics_events --before 2026-11-01 --yes
"""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from user.models import Event, User
from user.reporting_period import aware_range_for_local_dates

LOCAL_MARKERS = ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.0.")
DEFAULT_TEST_DOMAINS = ("paknomik.test", "example.com", "test.com")


class Command(BaseCommand):
    help = "Supprime les événements analytics de test (dry run par défaut, --yes pour supprimer)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--local",
            action="store_true",
            help="Événements d'un frontend local (referrer localhost, 127.0.0.1, réseau privé).",
        )
        parser.add_argument(
            "--test-accounts",
            action="store_true",
            help="Événements des comptes de test (domaines e-mail --email-domain).",
        )
        parser.add_argument(
            "--email-domain",
            action="append",
            default=[],
            help=f"Domaine e-mail de test (répétable). Défaut : {', '.join(DEFAULT_TEST_DOMAINS)}",
        )
        parser.add_argument(
            "--before",
            help="Événements créés avant cette date locale (AAAA-MM-JJ, exclue).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Tous les événements analytics (repartir de zéro).",
        )
        parser.add_argument("--yes", action="store_true", help="Supprimer réellement.")

    def handle(self, *args, **options):
        condition = self._condition(options)
        queryset = Event.objects.filter(condition)
        total = queryset.count()
        breakdown = queryset.values("event_type").annotate(total=Count("id")).order_by("-total")

        self.stdout.write(f"Événements concernés : {total}")
        for row in breakdown:
            self.stdout.write(f"  {row['event_type']}: {row['total']}")

        if not options["yes"]:
            self.stdout.write(self.style.WARNING("Dry run : rien n'a été supprimé. Ajoutez --yes."))
            return
        with transaction.atomic():
            deleted, _ = queryset.delete()
        self.stdout.write(self.style.SUCCESS(f"{deleted} événement(s) supprimé(s)."))

    def _condition(self, options) -> Q:
        if options["all"]:
            return Q()
        parts: list[Q] = []
        if options["local"]:
            local = Q()
            for marker in LOCAL_MARKERS:
                local |= Q(context__referrer__icontains=marker)
            parts.append(local)
        if options["test_accounts"]:
            domains = options["email_domain"] or list(DEFAULT_TEST_DOMAINS)
            emails = Q()
            for domain in domains:
                emails |= Q(email__iendswith=f"@{domain.lstrip('@')}")
            user_ids = list(User.objects.filter(emails).values_list("id", flat=True))
            parts.append(
                Q(actor_user_id__in=user_ids) | Q(context__triggered_by_buyer_id__in=user_ids)
            )
        if options["before"]:
            try:
                limit = date.fromisoformat(options["before"])
            except ValueError as exc:
                raise CommandError("--before attend une date AAAA-MM-JJ.") from exc
            lo, _ = aware_range_for_local_dates(limit, limit)
            parts.append(Q(created_at__lt=lo))
        if not parts:
            raise CommandError(
                "Choisissez au moins un filtre : --local, --test-accounts, --before ou --all."
            )
        condition = parts[0]
        for part in parts[1:]:
            condition |= part
        return condition
