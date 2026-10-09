from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from user import entitlements, market_dashboard_commercial, market_dashboard_visibility, market_dashboards
from user.events import log_search_with_appearances
from user.models import Event, Message, RFQRequest, Supplier, User
from user.platform_config import set_setting
from user.reporting_period import ReportPeriod


def make_event(event_type, minutes_ago, *, sid='sid-a', user=None, role=None, **context):
    role = role or (Event.ActorRole.BUYER if user else Event.ActorRole.VISITOR)
    payload = {'visitor_session_id': sid, 'geo_wilaya_label': 'Alger', **context}
    event = Event.objects.create(
        event_type=event_type, actor_role=role, actor_user=user, context=payload
    )
    Event.objects.filter(pk=event.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes_ago)
    )
    return event


def two_days():
    today = timezone.localdate()
    return today - timedelta(days=1), today


def today_period():
    start, end = two_days()
    return ReportPeriod('custom', start, end, start - timedelta(days=2), start - timedelta(days=1))


class SessionDerivationTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_inactivity_gap_splits_sessions(self):
        make_event(Event.EventType.BUYER_SEARCH, 100, query='carton')
        make_event(Event.EventType.BUYER_PRODUCT_VIEW, 90, source='search')
        make_event(Event.EventType.BUYER_SEARCH, 20, query='sac')
        sessions, _ = market_dashboards.build_sessions(*two_days())
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].duration_seconds, 600)
        self.assertEqual(sessions[1].duration_seconds, 0)

    def test_supplier_accounts_and_bots_are_excluded(self):
        supplier = Supplier.objects.create(company_name='Peer', email='peer@example.com')
        supplier_user = User.objects.create_user(
            username='peer', email='peer-user@example.com', password='x', supplier=supplier
        )
        make_event(Event.EventType.BUYER_SUPPLIER_VIEW, 10, sid='sid-s', user=supplier_user)
        make_event(Event.EventType.BUYER_SEARCH, 10, sid='sid-bot', device_class='bot', query='x')
        make_event(Event.EventType.BUYER_SEARCH, 10, sid='sid-ok', query='carton')
        sessions, _ = market_dashboards.build_sessions(*two_days())
        self.assertEqual(len(sessions), 1)

    def test_describe_reports_spread_and_max_label(self):
        stats = market_dashboards.describe([2, 4, 4, 4, 5, 5, 7, 9], list('abcdefgh'))
        self.assertEqual(stats['average'], 5.0)
        self.assertEqual(stats['stdDev'], 2.0)
        self.assertEqual(stats['maxLabel'], 'h')

    def test_session_behaviour_uses_all_sessions_as_denominator(self):
        make_event(Event.EventType.BUYER_SEARCH, 10, sid='a', query='carton')
        make_event(Event.EventType.BUYER_QUOTATION_REQUEST, 9, sid='a')
        make_event(Event.EventType.BUYER_SEARCH, 10, sid='b', query='carton')
        data = market_dashboards.build_sessions_dashboard(today_period(), 'day')
        rfq = next(row for row in data['behaviour'] if row['key'] == 'rfq')
        self.assertEqual((rfq['numerator'], rfq['denominator'], rfq['ratePct']), (1, 2, 50.0))
        untracked = next(row for row in data['behaviour'] if row['key'] == 'sample_request')
        self.assertFalse(untracked['tracked'])


class KeywordPrivacyTests(TestCase):
    def setUp(self):
        cache.clear()
        Supplier.objects.create(company_name='Devpac Emballage', email='devpac@example.com')

    def _search(self, sid, query, results=5):
        make_event(Event.EventType.BUYER_SEARCH, 10, sid=sid, query=query, results_count=results)

    def test_keyword_needs_minimum_distinct_sessions(self):
        for sid in ('a', 'b', 'c'):
            self._search(sid, 'Sachets alimentaires')
        self._search('a', 'gobelet')
        self._search('b', 'gobelet')
        data = market_dashboards.build_search_dashboard(today_period(), 'day')
        keys = [row['key'] for row in data['keywords']]
        self.assertIn('sachet alimentaire', keys)
        self.assertNotIn('gobelet', keys)
        self.assertEqual(data['hidden']['belowThreshold'], 2)

    def test_supplier_names_and_misspellings_are_masked(self):
        for sid in ('a', 'b', 'c'):
            self._search(sid, 'devpac')
            self._search(sid, 'devpak')
        data = market_dashboards.build_search_dashboard(today_period(), 'day')
        self.assertEqual(data['keywords'], [])
        self.assertEqual(data['hidden']['supplierNames'], 6)

    def test_lexical_similarity_is_lexical_only(self):
        left = market_dashboards.lexical_key('sachets alimentaires')
        right = market_dashboards.lexical_key('sachet pour aliment')
        self.assertGreaterEqual(market_dashboards.lexical_similarity(left, right), 0.5)
        self.assertEqual(market_dashboards.lexical_similarity('carton', 'gobelet'), 0.0)


class MarketDashboardApiTests(TestCase):
    def setUp(self):
        cache.clear()
        entitlements.seed_entitlements()
        self.client = APIClient()
        supplier = Supplier.objects.create(
            company_name='Viewer', email='viewer@example.com', paknomik_plan='business'
        )
        self.supplier_user = User.objects.create_user(
            username='viewer', email='viewer-user@example.com', password='x', supplier=supplier
        )
        self.buyer = User.objects.create_user(username='b', email='b@example.com', password='x')

    def test_buyers_cannot_read_market_dashboards(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.get(reverse('api_market_dashboard_sessions'))
        self.assertEqual(response.status_code, 403)

    def test_supplier_gets_aggregates_without_supplier_names(self):
        make_event(Event.EventType.BUYER_SUPPLIER_VIEW, 5, supplier_id=1)
        self.client.force_authenticate(self.supplier_user)
        response = self.client.get(
            reverse('api_market_dashboard_sessions'), {'period': 'today', 'granularity': 'weekday'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['series']), 7)
        self.assertNotIn('Viewer', str(response.data))

    def test_unknown_granularity_is_rejected(self):
        self.client.force_authenticate(self.supplier_user)
        response = self.client.get(
            reverse('api_market_dashboard_search'), {'granularity': 'supplier'}
        )
        self.assertEqual(response.status_code, 400)

    def test_visibility_and_commercial_endpoints(self):
        self.client.force_authenticate(self.supplier_user)
        visibility = self.client.get(
            reverse('api_market_dashboard_visibility'), {'period': '7d', 'granularity': 'geo'}
        )
        commercial = self.client.get(
            reverse('api_market_dashboard_commercial'), {'period': '7d', 'granularity': 'category'}
        )
        self.assertEqual((visibility.status_code, commercial.status_code), (200, 200))
        self.assertNotIn('Viewer', str(visibility.data) + str(commercial.data))
        geo_on_commercial = self.client.get(
            reverse('api_market_dashboard_commercial'), {'granularity': 'geo'}
        )
        self.assertEqual(geo_on_commercial.status_code, 400)


def make_appearance(supplier_id, *, display_format=None, buyer_id=None, search_id='s1'):
    context = {
        'supplier_id': supplier_id,
        'search_id': search_id,
        'source': 'products',
        'geo_wilaya_label': 'Alger',
    }
    if display_format:
        context['display_format'] = display_format
    if buyer_id:
        context['triggered_by_buyer_id'] = buyer_id
    return Event.objects.create(
        event_type=Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
        actor_role=Event.ActorRole.SYSTEM,
        context=context,
    )


class VisibilityDashboardTests(TestCase):
    def setUp(self):
        cache.clear()
        set_setting('market_min_suppliers', '5')

    def test_formats_used_by_few_suppliers_are_hidden(self):
        for supplier_id in range(1, 6):
            make_appearance(supplier_id, display_format='standard')
        make_appearance(99, display_format='vitrine_mega')
        make_appearance(98)
        data = market_dashboard_visibility.build_visibility_dashboard(today_period(), 'day')
        formats = data['formats']
        self.assertEqual([row['key'] for row in formats['rows']], ['standard'])
        self.assertEqual((formats['hidden'], formats['unrecorded']), (1, 1))
        self.assertTrue(data['concentration']['published'])
        self.assertEqual(data['concentration']['suppliers'], 7)

    def test_supplier_account_searches_are_excluded(self):
        supplier = Supplier.objects.create(company_name='Peer', email='peer@example.com')
        peer = User.objects.create_user(
            username='peer', email='peer-user@example.com', password='x', supplier=supplier
        )
        make_appearance(1, buyer_id=peer.pk)
        make_appearance(2)
        data = market_dashboard_visibility.build_visibility_dashboard(today_period(), 'day')
        self.assertEqual(data['kpis']['appearances']['total'], 1)
        self.assertFalse(data['concentration']['published'])

    def test_search_logging_records_display_format(self):
        supplier = Supplier.objects.create(company_name='Shown', email='shown@example.com')
        request = RequestFactory().get('/api/products/')
        request.user = AnonymousUser()
        log_search_with_appearances(
            request, search_query='carton', supplier_ids=[supplier.pk], results_count=1,
            source='products',
        )
        appearance = Event.objects.get(event_type=Event.EventType.SYSTEM_APPEAR_IN_SEARCH)
        self.assertEqual(appearance.context['display_format'], 'standard')


class CommercialDashboardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.suppliers = [
            Supplier.objects.create(company_name=f'S{index}', email=f's{index}@example.com')
            for index in range(3)
        ]

    def _activity(self, data, key):
        return next(row for row in data['activities'] if row['key'] == key)

    def test_default_threshold_publishes_a_single_supplier(self):
        RFQRequest.objects.create(supplier=self.suppliers[0], name='Acheteur')
        data = market_dashboard_commercial.build_commercial_dashboard(today_period(), 'day')
        self.assertEqual(self._activity(data, 'rfq')['total'], 1)

    def test_activity_needs_minimum_distinct_suppliers(self):
        set_setting('market_min_suppliers', '3')
        for supplier in self.suppliers[:2]:
            RFQRequest.objects.create(supplier=supplier, name='Acheteur')
        data = market_dashboard_commercial.build_commercial_dashboard(today_period(), 'day')
        rfq = self._activity(data, 'rfq')
        self.assertFalse(rfq['published'])
        self.assertIsNone(rfq['total'])
        self.assertTrue(all(row['values']['rfq'] is None for row in data['series']))

        set_setting('market_min_suppliers', '2')
        cache.clear()
        data = market_dashboard_commercial.build_commercial_dashboard(today_period(), 'day')
        self.assertEqual(self._activity(data, 'rfq')['total'], 2)

    def test_new_conversation_counts_first_message_per_pair(self):
        buyer = User.objects.create_user(username='buyer', email='buyer@example.com', password='x')
        for supplier in self.suppliers[:2]:
            for _ in range(3):
                Message.objects.create(supplier=supplier, buyer=buyer, sender=buyer, content='Bonjour')
        data = market_dashboard_commercial.build_commercial_dashboard(today_period(), 'week')
        self.assertEqual(self._activity(data, 'conversation')['total'], 2)

    def test_empty_period_is_published_as_zero(self):
        data = market_dashboard_commercial.build_commercial_dashboard(today_period(), 'category')
        order = self._activity(data, 'order')
        self.assertEqual((order['published'], order['total']), (True, 0))
        self.assertTrue(data['statuses']['orders']['published'])
