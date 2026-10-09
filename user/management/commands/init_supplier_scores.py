"""
Django management command.
Run: python manage.py init_supplier_scores
Initializes supplier score records for all suppliers.
"""

from django.core.management.base import BaseCommand

from user.models import Supplier, SupplierScore
from user.scoring import BASE_SCORE, backfill_supplier_score_from_history, ensure_supplier_score


class Command(BaseCommand):
    help = "Create supplier score records (base 50) and optionally backfill from history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--backfill",
            action="store_true",
            help="Rebuild scores from existing products, orders, RFQs, and favorites.",
        )

    def handle(self, *args, **options):
        backfill = options["backfill"]
        created = 0
        updated = 0

        for supplier in Supplier.objects.filter(is_active=True).iterator():
            if backfill:
                backfill_supplier_score_from_history(supplier.id)
                updated += 1
                continue

            score, was_created = SupplierScore.objects.get_or_create(
                supplier_id=supplier.id,
                defaults={"score_total": BASE_SCORE},
            )
            if was_created:
                created += 1
            elif score.score_total < BASE_SCORE:
                score.score_total = BASE_SCORE
                score.save(update_fields=["score_total", "updated_at"])

        if backfill:
            self.stdout.write(self.style.SUCCESS(f"Backfilled scores for {updated} suppliers."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Initialized {created} new supplier scores (base {BASE_SCORE})."
                )
            )
