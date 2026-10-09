from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from user.models import PragmadinarLedger, PragmadinarService, Supplier, User
from user.pragmadinar import get_or_create_wallet
from user.pragmadinar_consumption import build_consumption_dashboard
from user.reporting_period import ReportPeriod


def _at(day: date, hour=12):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)), timezone.get_current_timezone())


class ConsumptionDashboardTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(company_name="Alpha", email="alpha@example.com")
        self.user = User.objects.create_user("alpha", "alpha-u@example.com", "pw-123456", supplier=self.supplier)
        self.other = Supplier.objects.create(company_name="Beta", email="beta@example.com")
        self.search = PragmadinarService.objects.create(
            code="t_search", service_class="visibility", wallet_category="visibility", label="Apparition recherche"
        )
        self.visit = PragmadinarService.objects.create(
            code="t_visit", service_class="visibility", wallet_category="visibility", label="Visite boutique"
        )
        self.report = PragmadinarService.objects.create(
            code="t_report", service_class="commercial", wallet_category="analytics", label="Rapport marché"
        )
        self.today = timezone.localdate()
        self.period = ReportPeriod(
            "7d",
            self.today - timedelta(days=6),
            self.today,
            self.today - timedelta(days=13),
            self.today - timedelta(days=7),
        )

    def spend(self, supplier, category, service, amount, day, *, status=PragmadinarLedger.Status.POSTED,
              entry_type=PragmadinarLedger.EntryType.SPEND):
        entry = PragmadinarLedger.objects.create(
            wallet=get_or_create_wallet(supplier, category),
            service=service,
            entry_type=entry_type,
            status=status,
            amount=Decimal(amount),
            reason="test",
        )
        PragmadinarLedger.objects.filter(pk=entry.pk).update(created_at=_at(day))
        return entry

    def test_groups_by_wallet_service_and_day_with_cent_precision(self):
        d0 = self.today
        d1 = self.today - timedelta(days=1)
        self.spend(self.supplier, "visibility", self.search, "-0.01", d0)
        self.spend(self.supplier, "visibility", self.search, "-0.02", d0)
        self.spend(self.supplier, "visibility", self.visit, "-1.25", d1)
        self.spend(self.supplier, "analytics", self.report, "-10.00", d1)
        self.spend(self.supplier, "visibility", self.search, "-3.00", self.today - timedelta(days=8))

        data = build_consumption_dashboard(self.supplier, self.period)

        self.assertEqual(data["totals"]["consumed"], "11.28")
        self.assertEqual(data["totals"]["operations"], 4)
        self.assertEqual(data["totals"]["previousConsumed"], "3.00")
        self.assertEqual(data["totals"]["dailyAverage"], "1.61")
        self.assertEqual(data["totals"]["peakDay"], {"date": d1.isoformat(), "amount": "11.25"})

        wallets = {w["category"]: w for w in data["wallets"]}
        self.assertEqual(wallets["visibility"]["consumed"], "1.28")
        self.assertEqual(wallets["analytics"]["consumed"], "10.00")
        services = {s["code"]: s for s in wallets["visibility"]["services"]}
        self.assertEqual(services["t_search"]["consumed"], "0.03")
        self.assertEqual(services["t_search"]["operations"], 2)
        self.assertEqual(services["t_visit"]["consumed"], "1.25")
        self.assertEqual(wallets["visibility"]["services"][0]["code"], "t_visit")

        self.assertEqual(len(data["daily"]["dates"]), 7)
        self.assertEqual(data["daily"]["total"][-1], "0.03")
        self.assertEqual(data["daily"]["total"][-2], "11.25")
        self.assertEqual(data["daily"]["byCategory"]["analytics"][-2], "10.00")
        self.assertEqual(data["daily"]["total"][0], "0.00")

    def test_reversed_holds_and_other_suppliers_are_excluded(self):
        self.spend(self.supplier, "visibility", self.search, "-5.00", self.today, status=PragmadinarLedger.Status.REVERSED)
        self.spend(self.supplier, "visibility", self.search, "-4.00", self.today,
                   entry_type=PragmadinarLedger.EntryType.HOLD, status=PragmadinarLedger.Status.HELD)
        self.spend(self.other, "visibility", self.search, "-9.00", self.today)
        self.spend(self.supplier, "visibility", None, "-0.50", self.today)

        data = build_consumption_dashboard(self.supplier, self.period)

        self.assertEqual(data["totals"]["consumed"], "0.50")
        visibility = next(w for w in data["wallets"] if w["category"] == "visibility")
        self.assertEqual(visibility["services"][0]["code"], "__other__")
        self.assertNotIn("-", data["totals"]["previousConsumed"])

    def test_endpoint_is_supplier_scoped(self):
        self.spend(self.supplier, "visibility", self.search, "-2.50", self.today)
        client = APIClient()
        self.assertEqual(client.get("/api/supplier/wallet/consumption/").status_code, 401)
        buyer = User.objects.create_user("buyer", "buyer@example.com", "pw-123456")
        client.force_authenticate(buyer)
        self.assertEqual(client.get("/api/supplier/wallet/consumption/").status_code, 403)
        client.force_authenticate(self.user)
        response = client.get("/api/supplier/wallet/consumption/?period=7d")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["totals"]["consumed"], "2.50")
        self.assertEqual(response.data["period"]["preset"], "7d")
