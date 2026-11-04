"""Set every supplier's total Pragmadinar balance to 200, once.

Dry-run by default. This command is not run on startup or deploy.

    python manage.py reset_supplier_pragmadinar
    python manage.py reset_supplier_pragmadinar --apply
"""

from django.core.management.base import BaseCommand

from user.pragmadinar import reset_all_supplier_balances


class Command(BaseCommand):
    help = "Reset each supplier's total Pragmadinar balance to 200 (dry-run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the adjustment. Without this flag, only print the plan.",
        )

    def handle(self, *args, **options):
        report = reset_all_supplier_balances(apply=options["apply"])
        for row in report:
            state = "déjà fait" if row["already_reset"] else ("appliqué" if options["apply"] else "prévu")
            self.stdout.write(
                f"{row['supplier_id']} {row['company_name']}: "
                f"{row['previous_balance']} -> {row['new_balance']} ({state})"
            )
        self.stdout.write(self.style.SUCCESS(f"{len(report)} fournisseur(s)."))
