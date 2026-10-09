from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from user.campaigns import is_likely_bot
from user.middleware import VisitorSessionMiddleware
from user.models import Event, User
from user.visitor_tracking import VISITOR_SESSION_COOKIE, is_bot_user_agent

CHROME = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'
)


class BotDetectionTests(TestCase):
    def test_scripts_monitors_and_previews_are_bots(self):
        for ua in (
            'curl/8.4.0',
            'python-requests/2.31',
            'UptimeRobot/2.0',
            'facebookexternalhit/1.1',
            'WhatsApp/2.23.20',
            'Mozilla/5.0 HeadlessChrome/120.0',
        ):
            self.assertTrue(is_bot_user_agent(ua), ua)

    def test_real_browsers_are_not_bots(self):
        self.assertFalse(is_bot_user_agent(CHROME))
        request = RequestFactory().get('/', HTTP_USER_AGENT=CHROME)
        self.assertFalse(is_likely_bot(request))
        self.assertTrue(is_likely_bot(RequestFactory().get('/', HTTP_USER_AGENT='curl/8.4.0')))


class VisitorCookieTests(TestCase):
    def _response(self):
        middleware = VisitorSessionMiddleware(lambda request: HttpResponse())
        return middleware(RequestFactory().get('/'))

    @override_settings(SESSION_COOKIE_SECURE=True, VISITOR_COOKIE_SAMESITE='Lax')
    def test_cookie_is_secure_over_https(self):
        cookie = self._response().cookies[VISITOR_SESSION_COOKIE]
        self.assertTrue(cookie['secure'])
        self.assertEqual(cookie['samesite'], 'Lax')

    @override_settings(SESSION_COOKIE_SECURE=False, VISITOR_COOKIE_SAMESITE='None')
    def test_cross_site_cookie_forces_secure(self):
        cookie = self._response().cookies[VISITOR_SESSION_COOKIE]
        self.assertTrue(cookie['secure'])
        self.assertEqual(cookie['samesite'], 'None')


class PurgeAnalyticsEventsTests(TestCase):
    def setUp(self):
        self.tester = User.objects.create_user(
            username='tester', email='tester@paknomik.test', password='x'
        )
        self.real = User.objects.create_user(username='real', email='real@gmail.com', password='x')
        self.local = Event.objects.create(
            event_type=Event.EventType.BUYER_SEARCH,
            actor_role=Event.ActorRole.VISITOR,
            context={'referrer': 'http://localhost:5173/search?q=carton'},
        )
        self.test_account = Event.objects.create(
            event_type=Event.EventType.BUYER_PRODUCT_VIEW,
            actor_role=Event.ActorRole.BUYER,
            actor_user=self.tester,
            context={'referrer': 'https://paknomik.dz/'},
        )
        self.kept = Event.objects.create(
            event_type=Event.EventType.BUYER_PRODUCT_VIEW,
            actor_role=Event.ActorRole.BUYER,
            actor_user=self.real,
            context={'referrer': 'https://paknomik.dz/'},
        )

    def test_dry_run_deletes_nothing(self):
        out = StringIO()
        call_command('purge_analytics_events', '--local', '--test-accounts', stdout=out)
        self.assertIn('Événements concernés : 2', out.getvalue())
        self.assertEqual(Event.objects.count(), 3)

    def test_yes_deletes_only_matching_events(self):
        call_command(
            'purge_analytics_events', '--local', '--test-accounts', '--yes', stdout=StringIO()
        )
        self.assertEqual(list(Event.objects.values_list('id', flat=True)), [self.kept.id])

    def test_a_filter_is_required(self):
        with self.assertRaises(CommandError):
            call_command('purge_analytics_events', stdout=StringIO())
