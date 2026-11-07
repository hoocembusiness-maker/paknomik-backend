"""Welcome balance, removed charges, ultra-admin adjustments and ledger."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from user.models import PragmadinarLedger, PragmadinarWallet, Supplier
from user.pragmadinar import (
    credit_wallet,
    debit_service,
    grant_supplier_welcome_pragmadinar,
    reset_all_supplier_balances,
    seed_pragmadinar_catalog,
)
from user.scoring import handle_supplier_created
from user.tests_crm_pragmadinar import make_buyer, make_supplier

User = get_user_model()


class PragmadinarResetTests(TestCase):
    def setUp(self):
        seed_pragmadinar_catalog()
        self.supplier = make_supplier("Ancien")
        credit_wallet(self.supplier, Decimal("40"), wallet_category="visibility")
        credit_wallet(self.supplier, Decimal("15"), wallet_category="analytics")

    def test_reset_sets_every_supplier_total_to_200_once(self):
        other = make_supplier("Autre", email="autre@example.com")
        preview = reset_all_supplier_balances(apply=False)
        self.assertEqual(len(preview), 2)
        self.assertEqual(self.supplier.company_name, "Ancien")
        self.assertFalse(any(row["already_reset"] for row in preview))

        reset_all_supplier_balances(apply=True)
        self.assertEqual(self._total(self.supplier), Decimal("200"))
        self.assertEqual(self._total(other), Decimal("200"))
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.company_name, "Ancien")

        before_rows = PragmadinarLedger.objects.count()
        reset_all_supplier_balances(apply=True)
        self.assertEqual(self._total(self.supplier), Decimal("200"))
        self.assertEqual(PragmadinarLedger.objects.count(), before_rows)

    def test_new_supplier_receives_200_once(self):
        supplier = make_supplier("Neuf", email="neuf@example.com")
        handle_supplier_created(supplier)
        second = grant_supplier_welcome_pragmadinar(supplier)
        self.assertEqual(self._total(supplier), Decimal("200"))
        self.assertIsNone(second)
        self.assertEqual(
            PragmadinarLedger.objects.filter(
                wallet__supplier=supplier, entry_type="grant"
            ).count(),
            1,
        )

    def test_buyer_registration_does_not_grant_pragmadinar(self):
        client = APIClient()
        response = client.post(
            reverse("api_register"),
            {
                "email": "client@example.com",
                "password": "Testpass!2345",
                "name": "Client",
                "isSupplier": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(PragmadinarLedger.objects.filter(wallet__supplier__email="client@example.com").exists())
        self.assertFalse(Supplier.objects.filter(email="client@example.com").exists())

    def test_removed_activities_do_not_debit(self):
        before = self._total(self.supplier)
        for code in (
            "visibility.search_products",
            "visibility.store_visit",
            "visibility.product_visit",
            "visibility.store_favorited",
            "visibility.product_favorited",
            "commercial.orders",
            "commercial.quote_request",
            "commercial.messaging",
            "promo.sponsored_banner",
            "promo.keyword_reservation",
        ):
            self.assertIsNone(debit_service(self.supplier, code, reason="test"))
        self.assertEqual(self._total(self.supplier), before)

    def test_remaining_service_records_a_complete_spend(self):
        credit_wallet(self.supplier, Decimal("10"), wallet_category="analytics")
        entry = debit_service(
            self.supplier,
            "intel.market_overview",
            ref_type="market_report",
            ref_id=4,
            reason="Analyse de marché",
        )
        self.assertEqual(entry.service.code, "intel.market_overview")
        self.assertEqual(entry.ref_type, "market_report")
        self.assertEqual(entry.ref_id, 4)
        self.assertIsNotNone(entry.balance_before)
        self.assertEqual(entry.balance_after, entry.balance_before + entry.amount)
        self.assertLess(entry.amount, 0)

    def _total(self, supplier):
        return sum(
            (wallet.balance for wallet in PragmadinarWallet.objects.filter(supplier=supplier)),
            Decimal("0"),
        )


class UltraAdminPragmadinarTests(TestCase):
    def setUp(self):
        seed_pragmadinar_catalog()
        self.supplier = make_supplier("Géré")
        grant_supplier_welcome_pragmadinar(self.supplier)
        self.ultra = User.objects.create_user(
            username="hoocem",
            email="hoocem@example.com",
            password="Testpass!2345",
            is_staff=True,
            is_superuser=True,
        )
        self.buyer = make_buyer("outsider")
        self.client = APIClient()
        self.client.force_authenticate(self.ultra)

    def test_unauthorized_users_cannot_create_or_adjust(self):
        outsider = APIClient()
        outsider.force_authenticate(self.buyer)
        payload = {
            "username": "nouveau",
            "password": "Testpass!2345",
            "passwordConfirm": "Testpass!2345",
        }
        self.assertEqual(outsider.post(reverse("api_admin_supplier_create"), payload, format="json").status_code, 403)
        self.assertEqual(
            outsider.post(
                reverse("api_admin_supplier_balance", args=[self.supplier.pk]),
                {"mode": "add", "amount": "10", "reason": "test", "confirm": True},
                format="json",
            ).status_code,
            403,
        )

    def test_ultra_admin_creates_a_supplier_with_200(self):
        response = self.client.post(
            reverse("api_admin_supplier_create"),
            {
                "username": "boutique",
                "password": "Testpass!2345",
                "passwordConfirm": "Testpass!2345",
                "companyName": "Boutique créée",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["balance"], "200.00")
        created = Supplier.objects.get(company_name="Boutique créée")
        again = self.client.post(
            reverse("api_admin_supplier_create"),
            {
                "username": "boutique",
                "password": "Testpass!2345",
                "passwordConfirm": "Testpass!2345",
            },
            format="json",
        )
        self.assertEqual(again.status_code, 400)
        self.assertEqual(
            PragmadinarLedger.objects.filter(wallet__supplier=created, entry_type="grant").count(),
            1,
        )

    def test_adjustment_records_before_and_after(self):
        response = self.client.post(
            reverse("api_admin_supplier_balance", args=[self.supplier.pk]),
            {"mode": "add", "amount": "50", "reason": "Compensation", "confirm": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["balanceBefore"], "200.00")
        self.assertEqual(body["balanceAfter"], "250.00")
        self.assertEqual(body["source"], "administrative_adjustment")
        self.assertEqual(body["administratorId"], self.ultra.pk)

        refused = self.client.post(
            reverse("api_admin_supplier_balance", args=[self.supplier.pk]),
            {"mode": "subtract", "amount": "20", "reason": "Sans confirmation", "confirm": False},
            format="json",
        )
        self.assertEqual(refused.status_code, 400)

    def test_dashboard_filters_and_keeps_history(self):
        historical = credit_wallet(self.supplier, Decimal("5"), reason="Ancien crédit", wallet_category="visibility")
        debit_service(self.supplier, "intel.market_overview", reason="Rapport")
        summary = self.client.get(reverse("api_admin_pragmadinar_summary"))
        self.assertEqual(summary.status_code, 200)
        self.assertGreater(Decimal(summary.json()["registrationBonuses"]), 0)
        ledger = self.client.get(
            reverse("api_admin_pragmadinar_ledger"),
            {"supplierId": self.supplier.pk, "entryType": "spend", "service": "intel.market_overview"},
        )
        self.assertEqual(ledger.status_code, 200)
        self.assertEqual(ledger.json()["count"], 1)
        self.assertEqual(ledger.json()["results"][0]["serviceCode"], "intel.market_overview")
        self.assertTrue(PragmadinarLedger.objects.filter(pk=historical.pk).exists())
        detail = self.client.get(
            reverse("api_admin_pragmadinar_ledger_detail", args=[ledger.json()["results"][0]["id"]])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["refType"], "")

    def test_only_ultra_admin_can_hide_a_supplier(self):
        url = reverse("api_admin_supplier_visibility", args=[self.supplier.pk])
        staff = User.objects.create_user(
            username="staff", email="staff@example.com", password="Testpass!2345", is_staff=True
        )
        for user in (self.buyer, staff):
            other = APIClient()
            other.force_authenticate(user)
            self.assertEqual(other.post(url, {"hidden": True}, format="json").status_code, 403)
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_ghost)

    def test_ultra_admin_hides_and_shows_a_supplier(self):
        from user.models import AuditLog

        url = reverse("api_admin_supplier_visibility", args=[self.supplier.pk])
        self.assertEqual(self.client.post(url, {"hidden": "yes"}, format="json").status_code, 400)

        response = self.client.post(url, {"hidden": True}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["isHidden"])
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_ghost)
        rows = self.client.get(reverse("api_admin_pragmadinar_suppliers")).json()["results"]
        self.assertTrue(next(r for r in rows if r["supplierId"] == self.supplier.pk)["isHidden"])

        self.client.post(url, {"hidden": False}, format="json")
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_ghost)
        self.assertEqual(
            list(
                AuditLog.objects.filter(entity_id=self.supplier.pk, action__startswith="supplier.")
                .order_by("id")
                .values_list("action", flat=True)
            ),
            ["supplier.hide", "supplier.show"],
        )
        missing = reverse("api_admin_supplier_visibility", args=[999999])
        self.assertEqual(self.client.post(missing, {"hidden": True}, format="json").status_code, 404)
