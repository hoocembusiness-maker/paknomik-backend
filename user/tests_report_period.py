from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from user.models import Event, Supplier, User
from user.reporting_period import month_period, preset_period, resolve_report_period


class ReportPeriodResolutionTests(TestCase):
    today = date(2026, 10, 2)

    def test_rolling_presets_compare_with_previous_window_of_same_length(self):
        one = preset_period("1d", today=self.today)
        self.assertEqual((one.start, one.end), (self.today, self.today))
        self.assertEqual((one.prev_start, one.prev_end), (date(2026, 10, 1), date(2026, 10, 1)))

        week = preset_period("7d", today=self.today)
        self.assertEqual(week.start, date(2026, 9, 26))
        self.assertEqual(week.days, 7)
        self.assertEqual((week.prev_start, week.prev_end), (date(2026, 9, 19), date(2026, 9, 25)))

        month = preset_period("30d", today=self.today)
        self.assertEqual(month.days, 30)
        self.assertEqual(month.prev_end, month.start - timedelta(days=1))

    def test_month_to_date_and_last_month(self):
        mtd = preset_period("mtd", today=self.today)
        self.assertEqual((mtd.start, mtd.end), (date(2026, 10, 1), self.today))
        self.assertEqual((mtd.prev_start, mtd.prev_end), (date(2026, 9, 1), date(2026, 9, 2)))

        last = preset_period("last_month", today=self.today)
        self.assertEqual((last.start, last.end), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual((last.prev_start, last.prev_end), (date(2026, 8, 1), date(2026, 8, 31)))
        self.assertEqual(last.label, "sep 2026")

    def test_mtd_previous_window_is_clamped_to_shorter_month(self):
        mtd = preset_period("mtd", today=date(2026, 3, 31))
        self.assertEqual(mtd.prev_end, date(2026, 2, 28))

    def test_year_month_params_stay_backward_compatible(self):
        period = resolve_report_period({"year": "2026", "month": "10"}, today=self.today)
        self.assertEqual(period.preset, "month")
        self.assertEqual((period.start, period.end), (date(2026, 10, 1), self.today))
        self.assertEqual((period.prev_start, period.prev_end), (date(2026, 9, 1), date(2026, 9, 30)))

        past = month_period(2026, 8, today=self.today)
        self.assertEqual(past.end, date(2026, 8, 31))

    def test_preset_wins_and_invalid_values_fall_back(self):
        period = resolve_report_period({"period": "7d", "year": "2020", "month": "1"}, today=self.today)
        self.assertEqual(period.preset, "7d")
        fallback = resolve_report_period({"period": "bogus", "month": "abc"}, today=self.today)
        self.assertEqual(fallback.preset, "month")
        self.assertEqual(fallback.month, 10)


class DashboardPeriodEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.supplier = Supplier.objects.create(company_name="Period Co", email="period@example.com")
        self.seller = User.objects.create_user(
            username="period_seller", email="period-seller@example.com", password="x-pass-123"
        )
        self.seller.supplier = self.supplier
        self.seller.save(update_fields=["supplier"])
        self.staff = User.objects.create_user(
            username="period_staff", email="staff@example.com", password="x-pass-123", is_staff=True
        )

        now = timezone.now()
        recent = Event.objects.create(
            event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
            actor_role=Event.ActorRole.BUYER,
            context={"supplier_id": self.supplier.id},
        )
        old = Event.objects.create(
            event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
            actor_role=Event.ActorRole.BUYER,
            context={"supplier_id": self.supplier.id},
        )
        Event.objects.filter(pk=recent.pk).update(created_at=now)
        Event.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=10))

    def _profile_visits(self, payload):
        return payload["chart"]["totals"]["Visite de profil"]

    def test_supplier_dashboard_filters_by_rolling_preset(self):
        self.client.force_authenticate(self.seller)
        day = self.client.get("/api/supplier/dashboard/", {"period": "1d"}).json()
        month = self.client.get("/api/supplier/dashboard/", {"period": "30d"}).json()

        self.assertEqual(day["period"]["preset"], "1d")
        self.assertEqual(self._profile_visits(day), 1)
        self.assertEqual(self._profile_visits(month), 2)
        self.assertEqual(len(day["chart"]["labels"]), 1)

    def test_supplier_dashboard_still_accepts_year_month(self):
        self.client.force_authenticate(self.seller)
        today = timezone.localdate()
        payload = self.client.get(
            "/api/supplier/dashboard/", {"year": today.year, "month": today.month}
        ).json()
        self.assertEqual(payload["period"]["preset"], "month")
        self.assertEqual(payload["month"], today.month)

    def test_crm_dashboard_returns_resolved_period(self):
        self.client.force_authenticate(self.seller)
        response = self.client.get("/api/supplier/crm/dashboard/", {"period": "7d"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["period"]["days"], 7)

    def test_search_appearances_equal_store_plus_product_on_the_period(self):
        now = timezone.now()
        for source, count in (("products", 2), ("suppliers", 1)):
            for index in range(count):
                event = Event.objects.create(
                    event_type=Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
                    actor_role=Event.ActorRole.SYSTEM,
                    context={
                        "supplier_id": self.supplier.id,
                        "source": source,
                        "search_id": f"{source}-{index}",
                    },
                )
                Event.objects.filter(pk=event.pk).update(created_at=now)

        self.client.force_authenticate(self.seller)
        payload = self.client.get("/api/supplier/dashboard/", {"period": "1d"}).json()
        by_key = {item["key"]: item for item in payload["visibility"]}

        total = int(by_key["search_appearances"]["value"])
        store = int(by_key["store_appearances"]["value"])
        product = int(by_key["product_search_appearances"]["value"])

        self.assertEqual(len(payload["visibility"]), 14)
        self.assertEqual((total, store, product), (3, 1, 2))
        self.assertEqual(total, store + product)
        self.assertEqual(payload["chart"]["totals"]["Apparition"], total)
        self.assertEqual(
            by_key["product_search_appearances"]["label"],
            "Apparition de produits aux résultats de recherches",
        )
        self.assertEqual(
            by_key["product_appearance_share"]["label"],
            "Pourcentage d'apparition de produits par rapport au marché",
        )
        self.assertEqual(by_key["product_appearance_share"]["changeState"], "masked")

    def test_admin_dashboards_accept_presets(self):
        self.client.force_authenticate(self.staff)
        for url in (
            "/api/admin/intel/dashboard/",
            "/api/admin/usage/dashboard/",
            "/api/admin/commercial/dashboard/",
        ):
            response = self.client.get(url, {"period": "7d"})
            self.assertEqual(response.status_code, 200, url)
            body = response.json()
            self.assertEqual(body["period"]["preset"], "7d", url)
            self.assertEqual(len(body["chart"]["labels"]), 7, url)
