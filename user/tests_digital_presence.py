"""Digital presence: OAuth security, isolation, sync idempotency, nulls, permissions."""

import json
import os
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from user import rbac
from user.digital_config import PROVIDERS, validate_redirect_uri
from user.digital_crypto import decrypt_token, encrypt_token
from user.digital_http import scrub, use_transport
from user.digital_oauth import STATE_SALT
from user.digital_models import (
    DigitalAuthorization,
    DigitalConnection,
    DigitalMetric,
    DigitalOAuthAttempt,
    DigitalPlatform,
)
from user.digital_providers import _meta_day_from_end_time, record
from user.digital_reporting import build_comparison, metric_value
from user.digital_config import metric_spec
from user.digital_sync import sync_connection, upsert_records
from user.models import AuditLog, Supplier

User = get_user_model()

BASE = "https://api.paknomik.test"
ENV = {
    "APP_BASE_URL": BASE,
    "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "META_APP_ID": "meta-app",
    "META_APP_SECRET": "meta-secret",
    "META_REDIRECT_URI": f"{BASE}/api/integrations/oauth/meta/callback/",
    "INSTAGRAM_APP_ID": "ig-app",
    "INSTAGRAM_APP_SECRET": "ig-secret",
    "INSTAGRAM_REDIRECT_URI": f"{BASE}/api/integrations/oauth/instagram/callback/",
    "GOOGLE_CLIENT_ID": "google-client",
    "GOOGLE_CLIENT_SECRET": "google-secret",
    "GOOGLE_REDIRECT_URI": f"{BASE}/api/integrations/oauth/google/callback/",
}

GOOGLE_ACCESS = "ya29.ACCESS-SECRET-TOKEN-0123456789"
GOOGLE_REFRESH = "1//REFRESH-SECRET-TOKEN-0123456789"


class FakeProvider:
    """Ordered route table standing in for the network."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, method, contains, response):
        self.routes.append((method, contains, response))
        return self

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, body.decode() if body else ""))
        for route_method, contains, response in self.routes:
            if route_method == method and contains in url:
                if callable(response):
                    response = response(url, json.loads(body) if body and body[:1] == b"{" else body)
                status, payload = response
                return status, json.dumps(payload).encode()
        return 404, json.dumps({"error": {"code": 404, "message": "no fake route"}}).encode()

    def count(self, contains):
        return sum(1 for _m, url, _b in self.calls if contains in url)


def _param(url, name):
    return parse_qs(urlparse(url).query).get(name, [""])[0]


class DigitalPresenceBase(TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.supplier = Supplier.objects.create(company_name="Alpha Pack", email="alpha@example.com")
        self.user = User.objects.create_user(
            username="alpha", email="alpha-user@example.com", password="x-pass-123", supplier=self.supplier
        )
        self.other_supplier = Supplier.objects.create(company_name="Beta Pack", email="beta@example.com")
        self.other_user = User.objects.create_user(
            username="beta", email="beta-user@example.com", password="x-pass-123", supplier=self.other_supplier
        )

    def make_connection(self, supplier, platform, external_id, *, token="tok-resource", access="tok-access", refresh="", expires=None, **extra):
        authorization, _ = DigitalAuthorization.objects.get_or_create(
            supplier=supplier,
            platform=platform,
            defaults={
                "access_token_enc": encrypt_token(access),
                "refresh_token_enc": encrypt_token(refresh),
                "token_expires_at": expires,
                "scopes": list(PROVIDERS[platform].scopes),
            },
        )
        return DigitalConnection.objects.create(
            supplier=supplier,
            authorization=authorization,
            platform=platform,
            external_id=external_id,
            display_name=external_id,
            resource_type="test",
            resource_token_enc=encrypt_token(token) if platform == DigitalPlatform.META_FACEBOOK else "",
            **extra,
        )

    def start(self, platform):
        self.client.force_authenticate(self.user)
        response = self.client.post(f"/api/supplier/digital-presence/{platform}/connect/")
        self.assertEqual(response.status_code, 200, response.content)
        url = response.json()["authorizationUrl"]
        return _param(url, "state"), url

    def callback(self, slug, **params):
        return self.client.get(f"/api/integrations/oauth/{slug}/callback/", params)


class OAuthStateTests(DigitalPresenceBase):
    def google_routes(self, scope="https://www.googleapis.com/auth/webmasters.readonly"):
        return (
            FakeProvider()
            .add(
                "POST",
                "oauth2.googleapis.com/token",
                (200, {"access_token": GOOGLE_ACCESS, "refresh_token": GOOGLE_REFRESH, "expires_in": 3599, "scope": scope}),
            )
            .add(
                "GET",
                "webmasters/v3/sites",
                (
                    200,
                    {
                        "siteEntry": [
                            {"siteUrl": "https://www.alpha.dz/", "permissionLevel": "siteOwner"},
                            {"siteUrl": "https://other.dz/", "permissionLevel": "siteUnverifiedUser"},
                        ]
                    },
                ),
            )
            .add("POST", "oauth2.googleapis.com/revoke", (200, {}))
        )

    def test_full_google_flow_stores_encrypted_tokens_and_never_exposes_them(self):
        state, url = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        self.assertEqual(_param(url, "code_challenge_method"), "S256")
        self.assertEqual(_param(url, "scope"), "https://www.googleapis.com/auth/webmasters.readonly")
        self.assertEqual(_param(url, "redirect_uri"), ENV["GOOGLE_REDIRECT_URI"])
        payload = signing.loads(state, salt=STATE_SALT)
        self.assertEqual(set(payload), {"n", "p"})  # supplier lives server-side only

        fake = self.google_routes()
        with use_transport(fake):
            response = self.callback("google", state=state, code="auth-code")
        self.assertEqual(response.status_code, 302)
        self.assertIn("digital=connected", response["Location"])

        token_call = next(body for _m, u, body in fake.calls if "oauth2.googleapis.com/token" in u)
        self.assertIn("code_verifier=", token_call)

        authorization = DigitalAuthorization.objects.get(supplier=self.supplier)
        self.assertNotIn(GOOGLE_ACCESS, authorization.access_token_enc)
        self.assertEqual(decrypt_token(authorization.access_token_enc), GOOGLE_ACCESS)
        self.assertEqual(decrypt_token(authorization.refresh_token_enc), GOOGLE_REFRESH)
        connection = DigitalConnection.objects.get(supplier=self.supplier)
        self.assertEqual(connection.external_id, "https://www.alpha.dz/")  # only eligible site auto-selected

        self.client.force_authenticate(self.user)
        body = self.client.get("/api/supplier/digital-presence/").content.decode()
        for secret in (GOOGLE_ACCESS, GOOGLE_REFRESH, "google-secret", authorization.access_token_enc):
            self.assertNotIn(secret, body)
        audit = json.dumps(list(AuditLog.objects.values("new_value", "old_value", "reason")))
        self.assertNotIn(GOOGLE_ACCESS, audit)
        self.assertNotIn(GOOGLE_REFRESH, audit)

    def test_tampered_state_is_rejected(self):
        state, _ = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        fake = self.google_routes()
        with use_transport(fake):
            response = self.callback("google", state=state[:-2] + "xx", code="auth-code")
        self.assertIn("digitalError=invalid_state", response["Location"])
        self.assertEqual(fake.count("oauth2.googleapis.com/token"), 0)
        self.assertFalse(DigitalAuthorization.objects.exists())

    def test_state_cannot_be_replayed(self):
        state, _ = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        with use_transport(self.google_routes()):
            self.callback("google", state=state, code="auth-code")
            replay = self.callback("google", state=state, code="auth-code")
        self.assertIn("digitalError=invalid_state", replay["Location"])

    def test_expired_state_is_rejected(self):
        state, _ = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        future = timezone.now().timestamp() + 3600
        with mock.patch("django.core.signing.time.time", return_value=future), use_transport(self.google_routes()):
            response = self.callback("google", state=state, code="auth-code")
        self.assertIn("digitalError=invalid_state", response["Location"])

    def test_callback_from_another_browser_is_rejected(self):
        state, _ = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        stranger = APIClient()  # no binding cookie
        with use_transport(self.google_routes()):
            response = stranger.get("/api/integrations/oauth/google/callback/", {"state": state, "code": "c"})
        self.assertIn("digitalError=session_mismatch", response["Location"])
        self.assertFalse(DigitalAuthorization.objects.exists())

    def test_state_for_other_provider_slug_is_rejected(self):
        state, _ = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        with use_transport(self.google_routes()):
            response = self.callback("meta", state=state, code="auth-code")
        self.assertIn("digitalError=invalid_state", response["Location"])

    def test_supplier_id_from_browser_is_ignored(self):
        self.client.force_authenticate(self.user)
        self.client.post(
            f"/api/supplier/digital-presence/{DigitalPlatform.GOOGLE_SEARCH_CONSOLE}/connect/",
            {"supplierId": self.other_supplier.pk},
            format="json",
        )
        attempt = DigitalOAuthAttempt.objects.get()
        self.assertEqual(attempt.supplier_id, self.supplier.pk)

    def test_insufficient_scope_is_refused_and_nothing_stored(self):
        state, _ = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        with use_transport(self.google_routes(scope="openid")):
            response = self.callback("google", state=state, code="auth-code")
        self.assertIn("digitalError=insufficient_scope", response["Location"])
        self.assertFalse(DigitalAuthorization.objects.exists())

    def test_user_denial_is_reported(self):
        state, _ = self.start(DigitalPlatform.GOOGLE_SEARCH_CONSOLE)
        response = self.callback("google", state=state, error="access_denied")
        self.assertIn("digitalError=access_denied", response["Location"])

    def test_instagram_personal_account_is_not_eligible(self):
        state, url = self.start(DigitalPlatform.META_INSTAGRAM)
        self.assertEqual(_param(url, "scope"), "instagram_business_basic,instagram_business_manage_insights")
        self.assertNotIn("code_challenge", url)
        fake = (
            FakeProvider()
            .add(
                "POST",
                "api.instagram.com/oauth/access_token",
                (200, {"data": [{"access_token": "IGshort", "user_id": "1789", "permissions": "instagram_business_basic,instagram_business_manage_insights"}]}),
            )
            .add("GET", "graph.instagram.com/access_token", (200, {"access_token": "IGlong", "expires_in": 5183944}))
            .add("GET", "graph.instagram.com/v26.0/me", (200, {"user_id": "1789", "username": "alpha", "account_type": "PERSONAL"}))
        )
        with use_transport(fake):
            response = self.callback("instagram", state=state, code="c")
        self.assertIn("digital=connected", response["Location"])
        self.assertFalse(DigitalConnection.objects.exists())
        card = next(
            c
            for c in self.client.get("/api/supplier/digital-presence/").json()["cards"]
            if c["platform"] == DigitalPlatform.META_INSTAGRAM
        )
        self.assertEqual(card["state"], "no_eligible_resource")
        self.assertIn("personnel", card["stateMessage"])

    def test_meta_flow_uses_read_only_scopes_and_pages_pagination(self):
        state, url = self.start(DigitalPlatform.META_FACEBOOK)
        self.assertEqual(_param(url, "scope"), "pages_show_list,pages_read_engagement,read_insights")
        for forbidden in ("ads_read", "pages_manage", "publish", "messages"):
            self.assertNotIn(forbidden, url)
        fake = (
            FakeProvider()
            .add("GET", "oauth/access_token", lambda u, b: (200, {"access_token": "EAAlong" if "fb_exchange_token" in u else "EAAshort", "expires_in": 5183944}))
            .add("GET", "graph.facebook.com/v26.0/me/permissions", (200, {"data": [{"permission": p, "status": "granted"} for p in PROVIDERS[DigitalPlatform.META_FACEBOOK].scopes]}))
            .add(
                "GET",
                "me/accounts?after=CURSOR",
                (200, {"data": [{"id": "P2", "name": "Page 2", "tasks": ["ADVERTISE"], "access_token": "PAGE2TOKEN"}]}),
            )
            .add(
                "GET",
                "me/accounts",
                (
                    200,
                    {
                        "data": [{"id": "P1", "name": "Page 1", "tasks": ["ANALYZE"], "access_token": "PAGE1TOKEN"}],
                        "paging": {"next": "https://graph.facebook.com/v26.0/me/accounts?after=CURSOR"},
                    },
                ),
            )
            .add("GET", "graph.facebook.com/v26.0/me", (200, {"id": "U1", "name": "Alpha Admin"}))
        )
        with use_transport(fake):
            self.callback("meta", state=state, code="c")
        authorization = DigitalAuthorization.objects.get()
        self.assertEqual([r["id"] for r in authorization.available_resources], ["P1", "P2"])
        self.assertNotIn("PAGE1TOKEN", json.dumps(authorization.available_resources))
        connection = DigitalConnection.objects.get()  # only P1 has ANALYZE
        self.assertEqual(connection.external_id, "P1")
        self.assertEqual(decrypt_token(connection.resource_token_enc), "PAGE1TOKEN")


class IsolationAndPermissionTests(DigitalPresenceBase):
    def test_supplier_cannot_read_or_touch_another_supplier(self):
        mine = self.make_connection(self.supplier, DigitalPlatform.GOOGLE_ANALYTICS, "properties/1")
        theirs = self.make_connection(self.other_supplier, DigitalPlatform.GOOGLE_ANALYTICS, "properties/2")
        self.client.force_authenticate(self.user)
        payload = self.client.get("/api/supplier/digital-presence/").json()
        ids = {s["id"] for s in payload["sources"]}
        self.assertEqual(ids, {mine.pk})
        self.assertEqual(self.client.post(f"/api/supplier/digital-presence/connections/{theirs.pk}/sync/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/supplier/digital-presence/connections/{theirs.pk}/").status_code, 404)
        self.assertTrue(DigitalConnection.objects.filter(pk=theirs.pk).exists())

    def test_admin_views_require_ultra_admin(self):
        rbac.seed_roles()
        limited = User.objects.create_user(
            username="general", email="general@example.com", password="x-pass-123", is_staff=True
        )
        rbac.grant_role(limited, rbac.ADMIN_GENERAL)
        ultra = User.objects.create_user(
            username="ultra", email="ultra@example.com", password="x-pass-123", is_staff=True, is_superuser=True
        )
        self.make_connection(self.supplier, DigitalPlatform.GOOGLE_ANALYTICS, "properties/1", access=GOOGLE_ACCESS)

        for user, expected in ((self.user, 403), (limited, 403), (ultra, 200)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get("/api/admin/digital-presence/").status_code, expected)
            self.assertEqual(
                self.client.get(f"/api/admin/digital-presence/suppliers/{self.supplier.pk}/").status_code, expected
            )

        self.client.force_authenticate(ultra)
        body = self.client.get(f"/api/admin/digital-presence/suppliers/{self.supplier.pk}/").content.decode()
        self.assertNotIn(GOOGLE_ACCESS, body)
        self.assertNotIn("access_token_enc", body)

    def test_disconnect_deletes_tokens_connections_and_measures(self):
        connection = self.make_connection(
            self.supplier, DigitalPlatform.GOOGLE_ANALYTICS, "properties/1", access=GOOGLE_ACCESS, refresh=GOOGLE_REFRESH
        )
        today = timezone.localdate()
        upsert_records(connection, [record("sessions", 4, "count", today, today, "day")])
        fake = FakeProvider().add("POST", "oauth2.googleapis.com/revoke", (200, {}))
        self.client.force_authenticate(self.user)
        with use_transport(fake):
            response = self.client.delete(f"/api/supplier/digital-presence/{DigitalPlatform.GOOGLE_ANALYTICS}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["revokedAtProvider"])
        self.assertFalse(DigitalAuthorization.objects.filter(supplier=self.supplier).exists())
        self.assertFalse(DigitalConnection.objects.filter(supplier=self.supplier).exists())
        self.assertFalse(DigitalMetric.objects.filter(supplier=self.supplier).exists())

    def test_manual_sync_is_rate_limited(self):
        connection = self.make_connection(self.supplier, DigitalPlatform.META_FACEBOOK, "P1")
        fake = FakeProvider().add("GET", "graph.facebook.com", (200, {"data": []}))
        self.client.force_authenticate(self.user)
        with use_transport(fake):
            first = self.client.post(f"/api/supplier/digital-presence/connections/{connection.pk}/sync/")
            second = self.client.post(f"/api/supplier/digital-presence/connections/{connection.pk}/sync/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["code"], "sync_rate_limited")


class SyncTests(DigitalPresenceBase):
    def ga_routes(self, today):
        end = today - timedelta(days=1)

        def run_report(url, body):
            if body.get("dimensions"):
                rows = [
                    {"dimensionValues": [{"value": (end - timedelta(days=i)).strftime("%Y%m%d")}], "metricValues": [{"value": "10"}, {"value": "7"}, {"value": "20"}, {"value": "0.5"}]}
                    for i in range(3)
                ]
                page = rows[body["offset"] : body["offset"] + 2]
                return 200, {"rows": page, "rowCount": 3}
            return 200, {"rows": [{"metricValues": [{"value": "30"}, {"value": "15"}, {"value": "60"}, {"value": "0.52"}]}], "rowCount": 1}

        return (
            FakeProvider()
            .add("GET", "properties/123/keyEvents", (200, {}))
            .add("GET", "analyticsadmin.googleapis.com/v1beta/properties/123", (200, {"timeZone": "Africa/Algiers"}))
            .add(
                "GET",
                "properties/123/metadata",
                (200, {"metrics": [{"apiName": n} for n in ("sessions", "activeUsers", "screenPageViews", "engagementRate", "keyEvents")]}),
            )
            .add("POST", ":runReport", run_report)
        )

    def test_ga4_pagination_and_repeated_sync_without_duplicates(self):
        connection = self.make_connection(
            self.supplier,
            DigitalPlatform.GOOGLE_ANALYTICS,
            "properties/123",
            access=GOOGLE_ACCESS,
            expires=timezone.now() + timedelta(hours=1),
        )
        today = timezone.localdate()
        fake = self.ga_routes(today)
        with use_transport(fake):
            self.assertEqual(sync_connection(connection, today=today)["status"], "ok")
            count = DigitalMetric.objects.filter(connection=connection).count()
            sync_connection(connection, today=today)
        self.assertEqual(DigitalMetric.objects.filter(connection=connection).count(), count)
        daily_sessions = DigitalMetric.objects.filter(connection=connection, metric_key="sessions", granularity="day")
        self.assertEqual(daily_sessions.count(), 3)  # 2 + 1 rows across two pages
        connection.refresh_from_db()
        self.assertEqual(connection.source_timezone, "Africa/Algiers")
        self.assertEqual(connection.metadata["unsupportedMetrics"]["keyEvents"]["code"], "no_key_events")

        self.client.force_authenticate(self.user)
        source = self.client.get("/api/supplier/digital-presence/", {"days": 7}).json()["sources"][0]
        keys = [k["key"] for k in source["kpis"]]
        self.assertNotIn("keyEvents", keys)  # only shown when the property defines key events
        sessions = next(k for k in source["kpis"] if k["key"] == "sessions")
        self.assertEqual(sessions["value"], 30.0)
        series = next(s for s in source["series"] if s["key"] == "sessions")
        self.assertEqual(len(series["points"]), 7)
        self.assertEqual(series["missingDays"], 4)
        self.assertIsNone(series["points"][0]["value"])  # gap, not zero

    def test_expired_google_token_requires_reconnection(self):
        connection = self.make_connection(
            self.supplier,
            DigitalPlatform.GOOGLE_ANALYTICS,
            "properties/123",
            access="old",
            refresh=GOOGLE_REFRESH,
            expires=timezone.now() - timedelta(minutes=5),
        )
        fake = FakeProvider().add("POST", "oauth2.googleapis.com/token", (400, {"error": "invalid_grant"}))
        with use_transport(fake):
            outcome = sync_connection(connection)
        self.assertEqual(outcome["code"], "token_expired")
        connection.refresh_from_db()
        self.assertEqual(connection.status, DigitalConnection.Status.NEEDS_REAUTH)
        self.assertEqual(connection.authorization.status, DigitalAuthorization.Status.NEEDS_REAUTH)
        self.client.force_authenticate(self.user)
        card = next(
            c
            for c in self.client.get("/api/supplier/digital-presence/").json()["cards"]
            if c["platform"] == DigitalPlatform.GOOGLE_ANALYTICS
        )
        self.assertEqual(card["state"], "needs_reauth")

    def test_google_token_is_refreshed_before_expiry(self):
        connection = self.make_connection(
            self.supplier,
            DigitalPlatform.GOOGLE_ANALYTICS,
            "properties/123",
            access="old",
            refresh=GOOGLE_REFRESH,
            expires=timezone.now() + timedelta(seconds=30),
        )
        fake = self.ga_routes(timezone.localdate())
        fake.routes.insert(0, ("POST", "oauth2.googleapis.com/token", (200, {"access_token": "fresh-token", "expires_in": 3599})))
        with use_transport(fake):
            self.assertEqual(sync_connection(connection)["status"], "ok")
        connection.authorization.refresh_from_db()
        self.assertEqual(decrypt_token(connection.authorization.access_token_enc), "fresh-token")

    def test_missing_token_and_meta_revocation_mark_reauth(self):
        connection = self.make_connection(self.supplier, DigitalPlatform.META_FACEBOOK, "P1", token="")
        self.assertEqual(sync_connection(connection)["code"], "token_expired")

        connection = self.make_connection(self.other_supplier, DigitalPlatform.META_FACEBOOK, "P9")
        fake = FakeProvider().add(
            "GET", "graph.facebook.com", (400, {"error": {"code": 190, "message": "Error validating access token EAAsecretvalue"}})
        )
        with use_transport(fake):
            outcome = sync_connection(connection)
        self.assertEqual(outcome["code"], "token_expired")
        connection.refresh_from_db()
        self.assertEqual(connection.status, DigitalConnection.Status.NEEDS_REAUTH)
        self.assertNotIn("EAAsecretvalue", connection.last_error_message)

    def test_facebook_unsupported_metric_and_missing_days_stay_null(self):
        connection = self.make_connection(self.supplier, DigitalPlatform.META_FACEBOOK, "P1")
        today = timezone.localdate()
        end = today - timedelta(days=1)

        def insights(url, _body):
            metric = _param(url, "metric")
            if metric == "page_post_engagements":
                return 400, {"error": {"code": 100, "message": "(#100) The value must be a valid insights metric"}}
            if metric == "page_total_media_view_unique":
                return 200, {"data": [{"values": [{"value": 321, "end_time": f"{(end + timedelta(days=1)).isoformat()}T07:00:00+0000"}]}]}
            days = [end - timedelta(days=i) for i in range(7) if i != 2]  # one day missing
            return 200, {"data": [{"values": [{"value": 5, "end_time": f"{(d + timedelta(days=1)).isoformat()}T07:00:00+0000"} for d in days]}]}

        fake = (
            FakeProvider()
            .add("GET", "/P1/insights", insights)
            .add("GET", "/P1/posts", (200, {"data": []}))
        )
        with use_transport(fake):
            sync_connection(connection, today=today)
        connection.refresh_from_db()
        self.assertIn("page_post_engagements", connection.metadata["unsupportedMetrics"])

        start = end - timedelta(days=6)
        views = metric_value(connection, metric_spec(DigitalPlatform.META_FACEBOOK, "page_media_view"), start, end)
        self.assertIsNone(views["value"])
        self.assertEqual(views["status"], "partial")
        self.assertEqual(views["daysWithData"], 6)
        engagement = metric_value(connection, metric_spec(DigitalPlatform.META_FACEBOOK, "page_post_engagements"), start, end)
        self.assertEqual(engagement["status"], "unavailable")
        unique = metric_value(connection, metric_spec(DigitalPlatform.META_FACEBOOK, "page_total_media_view_unique"), start, end)
        self.assertEqual(unique["value"], 321.0)
        unique_90 = metric_value(
            connection, metric_spec(DigitalPlatform.META_FACEBOOK, "page_total_media_view_unique"), end - timedelta(days=89), end
        )
        self.assertIsNone(unique_90["value"])

        calls_before = fake.count("metric=page_post_engagements")
        DigitalConnection.objects.filter(pk=connection.pk).update(last_synced_at=None)
        connection.refresh_from_db()
        with use_transport(fake):
            sync_connection(connection, today=today)
        self.assertEqual(fake.count("metric=page_post_engagements"), calls_before)  # cached as unsupported

    def test_null_value_is_stored_as_null_not_zero(self):
        connection = self.make_connection(self.supplier, DigitalPlatform.GOOGLE_SEARCH_CONSOLE, "https://a.dz/")
        day = timezone.localdate()
        upsert_records(connection, [record("clicks", None, "count", day, day, "day")])
        self.assertIsNone(DigitalMetric.objects.get(connection=connection).value)

    def test_search_console_dates_timezone_and_top_rows(self):
        connection = self.make_connection(
            self.supplier,
            DigitalPlatform.GOOGLE_SEARCH_CONSOLE,
            "sc-domain:alpha.dz",
            access=GOOGLE_ACCESS,
            expires=timezone.now() + timedelta(hours=1),
        )
        today = timezone.localdate()
        end = today - timedelta(days=3)

        def query(url, body):
            self.assertIn("sc-domain%3Aalpha.dz", url)
            self.assertEqual(body["dataState"], "final")
            dims = body.get("dimensions")
            if dims == ["date"]:
                return 200, {"rows": [{"keys": [end.isoformat()], "clicks": 3, "impressions": 90, "ctr": 0.033, "position": 7.5}]}
            if dims == ["query"]:
                return 200, {"rows": [{"keys": ["carton"], "clicks": 2, "impressions": 40, "ctr": 0.05, "position": 4.1}]}
            if dims:
                return 200, {}
            return 200, {"rows": [{"clicks": 3, "impressions": 90, "ctr": 0.033, "position": 7.5}]}

        with use_transport(FakeProvider().add("POST", "searchAnalytics/query", query)):
            sync_connection(connection, today=today)
        connection.refresh_from_db()
        self.assertEqual(connection.data_end, end)
        self.assertEqual(connection.source_timezone, "America/Los_Angeles")
        self.client.force_authenticate(self.user)
        source = self.client.get("/api/supplier/digital-presence/", {"days": 7}).json()["sources"][0]
        queries = next(t for t in source["tops"] if t["dimension"] == "query")
        self.assertEqual(queries["rows"][0]["label"], "carton")
        self.assertIn("pas un export exhaustif", queries["note"])
        self.assertEqual(source["periodEnd"], end.isoformat())


class ComparisonAndHelpersTests(DigitalPresenceBase):
    def test_comparison_is_limited_to_one_metric_source_and_window(self):
        end = timezone.localdate() - timedelta(days=3)
        start = end - timedelta(days=27)
        a = self.make_connection(self.supplier, DigitalPlatform.GOOGLE_SEARCH_CONSOLE, "https://a.dz/", data_end=end)
        b = self.make_connection(self.other_supplier, DigitalPlatform.GOOGLE_SEARCH_CONSOLE, "https://b.dz/", data_end=end)
        upsert_records(a, [record("clicks", 120, "count", start, end, "window")])
        upsert_records(b, [record("clicks", 80, "count", start - timedelta(days=1), end - timedelta(days=1), "window")])

        payload = build_comparison(platform=DigitalPlatform.GOOGLE_SEARCH_CONSOLE, metric="clicks", days=28)
        self.assertEqual([r["supplierId"] for r in payload["rows"]], [self.supplier.pk])
        self.assertEqual(payload["rows"][0]["value"], 120.0)
        self.assertEqual([r["supplierId"] for r in payload["excluded"]], [self.other_supplier.pk])
        self.assertEqual(payload["periodStart"], start.isoformat())

        ultra = User.objects.create_user(
            username="ultra2", email="ultra2@example.com", password="x-pass-123", is_staff=True, is_superuser=True
        )
        self.client.force_authenticate(ultra)
        mixed = self.client.get(
            "/api/admin/digital-presence/compare/",
            {"platform": DigitalPlatform.GOOGLE_SEARCH_CONSOLE, "metric": "sessions", "days": 28},
        )
        self.assertEqual(mixed.status_code, 400)
        self.assertEqual(mixed.json()["code"], "not_comparable")

    def test_meta_end_time_maps_to_previous_day(self):
        self.assertEqual(str(_meta_day_from_end_time("2026-09-02T07:00:00+0000")), "2026-09-01")

    def test_scrub_masks_tokens(self):
        text = "failed url?access_token=EAAB123&x=1 Bearer ya29.abc {\"refresh_token\": \"1//xyz\"}"
        cleaned = scrub(text)
        for secret in ("EAAB123", "ya29.abc", "1//xyz"):
            self.assertNotIn(secret, cleaned)

    def test_redirect_uri_validation(self):
        config = PROVIDERS[DigitalPlatform.GOOGLE_ANALYTICS]
        cases = {
            f"{BASE}/api/integrations/oauth/google/callback/": True,
            "http://api.paknomik.test/api/integrations/oauth/google/callback/": False,
            f"{BASE}/api/integrations/oauth/google/callback/?next=x": False,
            f"{BASE}/evil/": False,
            "https://evil.example/api/integrations/oauth/google/callback/": False,
        }
        for uri, ok in cases.items():
            with mock.patch.dict(os.environ, {"GOOGLE_REDIRECT_URI": uri}):
                accepted, _problem = validate_redirect_uri(config)
                self.assertEqual(bool(accepted), ok, uri)

    def test_logs_never_contain_tokens(self):
        connection = self.make_connection(self.supplier, DigitalPlatform.META_FACEBOOK, "P1", token="PAGESECRET123")
        fake = FakeProvider().add("GET", "graph.facebook.com", (500, {"error": {"code": 2, "message": "boom", "is_transient": True}}))
        with self.assertLogs("user.digital_sync", level="INFO") as logs, use_transport(fake):
            sync_connection(connection)
        self.assertTrue(logs.output)
        self.assertNotIn("PAGESECRET123", "\n".join(logs.output))
        connection.refresh_from_db()
        self.assertNotIn("PAGESECRET123", connection.last_error_message)

    def test_unconfigured_platform_cannot_start(self):
        self.client.force_authenticate(self.user)
        with mock.patch.dict(os.environ, {"META_APP_ID": ""}):
            response = self.client.post(f"/api/supplier/digital-presence/{DigitalPlatform.META_FACEBOOK}/connect/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "not_configured")
        card = next(
            c
            for c in self.client.get("/api/supplier/digital-presence/").json()["cards"]
            if c["platform"] == DigitalPlatform.META_INSTAGRAM
        )
        self.assertTrue(card["configured"])
