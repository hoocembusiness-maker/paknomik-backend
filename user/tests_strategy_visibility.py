from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from user.api.strategy_views import _service_options
from user.models import PragmadinarLedger, Supplier
from user.pragmadinar import seed_pragmadinar_catalog

User = get_user_model()


class StrategyVisibilityServicesTests(TestCase):
    def test_visibility_services_stay_listed_when_they_are_not_billed(self):
        seed_pragmadinar_catalog()
        options = [row for row in _service_options() if row["walletCategory"] == "visibility"]
        codes = {row["serviceCode"] for row in options}
        self.assertIn("visibility.vitrine_extra", codes)
        self.assertIn("visibility.card_horizontal", codes)
        self.assertIn("visibility.search_store", codes)
        self.assertNotIn("visibility.vitrine_mega_search", codes)
        self.assertNotIn("visibility.store_favorited", codes)
        for code in ("visibility.vitrine_extra", "visibility.card_horizontal", "visibility.search_store"):
            row = next(item for item in options if item["serviceCode"] == code)
            self.assertGreater(Decimal(row["costPragmadinar"]), 0)
            self.assertTrue(row["included"])

    def test_price_list_includes_visibility_and_promotion_services(self):
        seed_pragmadinar_catalog()
        client = APIClient()
        response = client.get("/api/pragmadinar/catalog/")
        self.assertEqual(response.status_code, 200)
        by_code = {row["code"]: row for row in response.json()["services"]}
        self.assertEqual(by_code["visibility.vitrine_extra"]["costPragmadinar"], "0.40")
        self.assertIn("dans la boutique", by_code["visibility.vitrine_extra"]["label"])
        self.assertEqual(by_code["visibility.search_store"]["costPragmadinar"], "0.10")
        self.assertEqual(by_code["intel.report_monthly"]["costPragmadinar"], "2.00")
        self.assertEqual(by_code["intel.report_monthly"]["priceRank"], 10)
        self.assertEqual(by_code["intel.report_activity_quarterly"]["costPragmadinar"], "4.00")
        self.assertEqual(by_code["intel.report_activity_quarterly"]["priceRank"], 7)
        self.assertTrue(by_code["promo.sponsored_banner"]["onPriceList"])
        self.assertTrue(by_code["commercial.orders"]["onPriceList"])
        self.assertFalse(by_code["visibility.store_favorited"]["onPriceList"])
        self.assertFalse(by_code["commercial.pak_dating"]["onPriceList"])
        self.assertNotIn("visibility.product_compared", by_code)

    def test_a_service_is_listed_in_only_one_wallet(self):
        seed_pragmadinar_catalog()
        wallets_by_code = {}
        for row in _service_options():
            wallets_by_code.setdefault(row["serviceCode"], set()).add(row["walletCategory"])
        duplicated = {
            code: wallets
            for code, wallets in wallets_by_code.items()
            if len(wallets) > 1
        }
        self.assertEqual(duplicated, {})
        self.assertFalse(any(row["key"].startswith("campaign:") for row in _service_options()))
        promotion = {
            row["serviceCode"]
            for row in _service_options()
            if row["walletCategory"] == "promotion"
        }
        self.assertIn("visibility.vitrine_mega_search", promotion)
        self.assertIn("visibility.card_horizontal_search", promotion)
        self.assertIn("promo.sponsored_banner", promotion)
        self.assertNotIn("promo.quote_template", promotion)
        self.assertNotIn("commercial.pak_dating", {row["serviceCode"] for row in _service_options()})

    def test_recording_a_visibility_objective_reserves_the_catalog_price(self):
        from user.pragmadinar import credit_wallet

        seed_pragmadinar_catalog()
        supplier = Supplier.objects.create(company_name="Embalium", email="embalium-strategy@example.com")
        user = User.objects.create_user(
            username="embalium-strategy",
            email="embalium-strategy@example.com",
            password="secret-pass",
            supplier=supplier,
        )
        credit_wallet(supplier, Decimal("20"), wallet_category="visibility")
        client = APIClient()
        client.force_authenticate(user)
        start = timezone.localdate() + timedelta(days=1)
        response = client.post(
            "/api/supplier/wallet/strategy/",
            {
                "walletCategory": "visibility",
                "serviceCode": "visibility.vitrine_extra",
                "targetQuantity": 10,
                "startDate": start.isoformat(),
                "endDate": (start + timedelta(days=6)).isoformat(),
                "rhythm": "variable",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["reservation"]["reservedAmount"], "4.00")
        hold = PragmadinarLedger.objects.get(entry_type=PragmadinarLedger.EntryType.HOLD)
        self.assertEqual(hold.amount, Decimal("-4.00"))
        self.assertFalse(PragmadinarLedger.objects.filter(entry_type=PragmadinarLedger.EntryType.SPEND).exists())

    def test_an_open_ended_reservation_has_no_end_date(self):
        from user.pragmadinar import credit_wallet

        seed_pragmadinar_catalog()
        supplier = Supplier.objects.create(company_name="Embalium", email="embalium-open@example.com")
        user = User.objects.create_user(
            username="embalium-open",
            email="embalium-open@example.com",
            password="secret-pass",
            supplier=supplier,
        )
        credit_wallet(supplier, Decimal("20"), wallet_category="visibility")
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(
            "/api/supplier/wallet/strategy/",
            {
                "walletCategory": "visibility",
                "serviceCode": "visibility.vitrine_extra",
                "targetQuantity": 5,
                "openEnded": True,
                "rhythm": "variable",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        reservation = response.json()["reservation"]
        self.assertEqual(reservation["reservedAmount"], "2.00")
        self.assertEqual(reservation["startDate"], timezone.localdate().isoformat())
        self.assertIsNone(reservation["endDate"])
        self.assertIsNone(reservation["endsAt"])
        self.assertEqual(reservation["period"], "open")

    def test_balances_move_between_services_of_the_same_wallet(self):
        from user.models import PragmadinarStrategyReservation
        from user.pragmadinar import credit_wallet

        seed_pragmadinar_catalog()
        supplier = Supplier.objects.create(company_name="Embalium", email="embalium-move@example.com")
        user = User.objects.create_user(
            username="embalium-move",
            email="embalium-move@example.com",
            password="secret-pass",
            supplier=supplier,
        )
        credit_wallet(supplier, Decimal("20"), wallet_category="visibility")
        client = APIClient()
        client.force_authenticate(user)
        start = timezone.localdate() + timedelta(days=1)
        created = []
        for code, quantity in (("visibility.vitrine_extra", 10), ("visibility.card_horizontal", 4)):
            response = client.post(
                "/api/supplier/wallet/strategy/",
                {
                    "walletCategory": "visibility",
                    "serviceCode": code,
                    "targetQuantity": quantity,
                    "startDate": start.isoformat(),
                    "endDate": (start + timedelta(days=6)).isoformat(),
                    "rhythm": "variable",
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.content)
            created.append(response.json()["reservation"]["id"])
        moved = client.post(
            "/api/supplier/wallet/strategy/reservations/reallocate/",
            {"fromId": created[0], "toId": created[1], "amount": "1.00"},
            format="json",
        )
        self.assertEqual(moved.status_code, 200, moved.content)
        extra = PragmadinarStrategyReservation.objects.get(pk=created[0])
        card = PragmadinarStrategyReservation.objects.get(pk=created[1])
        self.assertEqual(extra.reserved_amount, Decimal("3.00"))
        self.assertEqual(card.reserved_amount, Decimal("3.00"))
        held = sum(
            abs(entry.amount)
            for entry in PragmadinarLedger.objects.filter(entry_type=PragmadinarLedger.EntryType.HOLD, status=PragmadinarLedger.Status.HELD)
        )
        self.assertEqual(held, Decimal("6.00"))
