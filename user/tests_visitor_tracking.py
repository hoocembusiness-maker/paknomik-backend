"""Tests for anonymous visitor geo and session enrichment."""

from django.test import RequestFactory, TestCase, override_settings

from user.events import log_buyer_event, log_supplier_product_view
from user.geo_analytics import breakdown_events_by_country, geo_context_for_actor
from user.models import Category, Event, Product, Supplier
from user.visitor_tracking import (
    ip_fingerprint,
    lookup_geo_from_request,
    request_analytics_context,
    visitor_session_id,
)


class VisitorTrackingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.supplier = Supplier.objects.create(
            company_name="Geo Pack",
            email="geo@example.com",
        )
        self.category = Category.objects.create(name_en="Bags", level=1)
        self.product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Bag",
            description="Test",
        )

    def test_cloudflare_country_is_captured(self):
        request = self.factory.get(
            "/api/products/",
            HTTP_CF_IPCOUNTRY="DZ",
            HTTP_CF_IPCITY="Algiers",
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0)",
            HTTP_REFERER="https://google.com/",
        )
        request.visitor_session_id = "test-session-uuid"
        ctx = request_analytics_context(request)
        self.assertEqual(ctx["geo_country"], "DZ")
        self.assertEqual(ctx["geo_city"], "Algiers")
        self.assertEqual(ctx["visitor_session_id"], "test-session-uuid")
        self.assertEqual(ctx["device_class"], "desktop")
        self.assertIn("geo_ip_hash", ctx)
        self.assertNotIn("127.0.0.1", str(ctx.values()))

    def test_geo_context_merges_into_events(self):
        request = self.factory.get(
            "/",
            HTTP_CF_IPCOUNTRY="DZ",
            HTTP_CF_IPCITY="Oran",
        )
        request.visitor_session_id = "sess-1"
        merged = geo_context_for_actor(actor_user=None, request=request)
        self.assertEqual(merged["geo_country"], "DZ")
        self.assertIn("Oran", merged["geo_wilaya_label"])

    def test_anonymous_product_view_logs_visitor_geo(self):
        request = self.factory.get(
            f"/api/products/{self.product.pk}/",
            HTTP_CF_IPCOUNTRY="DZ",
            HTTP_CF_IPCITY="Constantine",
        )
        request.visitor_session_id = "anon-sess"
        request.user = type("Anon", (), {"is_authenticated": False})()

        log_supplier_product_view(
            request,
            supplier_id=self.supplier.pk,
            product_id=self.product.pk,
        )
        event = Event.objects.get(event_type=Event.EventType.BUYER_PRODUCT_VIEW)
        self.assertEqual(event.actor_role, Event.ActorRole.VISITOR)
        self.assertIsNone(event.actor_user)
        self.assertEqual(event.context["geo_country"], "DZ")
        self.assertEqual(event.context["visitor_session_id"], "anon-sess")

    def test_country_breakdown_uses_ip_geo_for_visitors(self):
        log_buyer_event(
            None,
            Event.EventType.BUYER_SEARCH,
            {"search_query": "carton", "geo_country": "DZ", "geo_country_name": "Algeria"},
        )
        rows = breakdown_events_by_country(Event.objects.all())
        self.assertEqual(rows[0]["label"], "Algeria")
        self.assertEqual(rows[0]["count"], 1)

    @override_settings(
        GEOIP2_DATABASE_PATH="/nonexistent/path.mmdb",
        GEO_LOOKUP_HTTP_FALLBACK=False,
    )
    def test_missing_geoip_db_does_not_crash(self):
        request = self.factory.get("/", REMOTE_ADDR="8.8.8.8")
        self.assertEqual(lookup_geo_from_request(request), {})

    @override_settings(DEBUG=True, GEOIP2_DEV_FALLBACK_IP="")
    def test_localhost_uses_dev_client_ip_header_for_geo(self):
        import os
        from django.conf import settings as django_settings

        db_path = django_settings.GEOIP2_DATABASE_PATH
        if not db_path or not os.path.isfile(db_path):
            self.skipTest("GeoLite2 database not configured")

        request = self.factory.get(
            "/api/suppliers/1/",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_PAKNOMIK_CLIENT_IP="105.102.0.1",
        )
        ctx = lookup_geo_from_request(request)
        self.assertEqual(ctx.get("geo_country"), "DZ")
        self.assertEqual(ctx.get("geo_wilaya"), "algiers")
        merged = geo_context_for_actor(actor_user=None, request=request)
        self.assertIn("Alger", merged.get("geo_wilaya_label", ""))

    def test_ip_fingerprint_is_stable(self):
        self.assertEqual(ip_fingerprint("1.2.3.4"), ip_fingerprint("1.2.3.4"))
        self.assertNotEqual(ip_fingerprint("1.2.3.4"), ip_fingerprint("5.6.7.8"))
