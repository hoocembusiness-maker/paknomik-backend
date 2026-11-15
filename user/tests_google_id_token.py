from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from allauth.socialaccount.providers.oauth2.client import OAuth2Error

from user.google_id_token import (
    CERTS_CACHE_KEY,
    _fetch_certs,
    verify_google_id_token,
)


class GoogleIdTokenCertCacheTests(SimpleTestCase):
    def setUp(self):
        cache.delete(CERTS_CACHE_KEY)

    def tearDown(self):
        cache.delete(CERTS_CACHE_KEY)

    @patch('user.google_id_token.get_adapter')
    def test_fetch_certs_caches_response(self, get_adapter):
        response = MagicMock()
        response.json.return_value = {'kid-a': '-----BEGIN CERTIFICATE-----\nA'}
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get.return_value = response
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        get_adapter.return_value.get_requests_session.return_value = session

        first = _fetch_certs()
        second = _fetch_certs()

        self.assertEqual(first, second)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(cache.get(CERTS_CACHE_KEY), first)

    @patch('user.google_id_token.get_adapter')
    def test_fetch_certs_force_refresh(self, get_adapter):
        response = MagicMock()
        response.json.side_effect = [
            {'kid-a': 'cert-1'},
            {'kid-a': 'cert-2'},
        ]
        response.raise_for_status = MagicMock()
        session = MagicMock()
        session.get.return_value = response
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        get_adapter.return_value.get_requests_session.return_value = session

        first = _fetch_certs()
        second = _fetch_certs(force_refresh=True)

        self.assertEqual(first, {'kid-a': 'cert-1'})
        self.assertEqual(second, {'kid-a': 'cert-2'})
        self.assertEqual(session.get.call_count, 2)

    def test_verify_rejects_empty_token(self):
        with self.assertRaises(OAuth2Error):
            verify_google_id_token('', 'client-id')
