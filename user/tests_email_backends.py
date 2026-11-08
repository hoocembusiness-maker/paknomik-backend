import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from user.email_backends import RESEND_API_URL, RESEND_USER_AGENT, ResendEmailBackend


class _FakeResponse:
    def __init__(self, status=200, body=b'{"id":"email_1"}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@override_settings(RESEND_API_KEY='re_test_key', EMAIL_TIMEOUT=5)
class ResendEmailBackendTests(SimpleTestCase):
    def _message(self):
        message = EmailMultiAlternatives(
            'Sujet',
            'Corps texte',
            'Paknomik <no_reply@paknomik.com>',
            ['buyer@example.com'],
        )
        message.attach_alternative('<p>Corps HTML</p>', 'text/html')
        return message

    def test_posts_json_over_https(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['url'] = request.full_url
            captured['method'] = request.get_method()
            captured['timeout'] = timeout
            captured['authorization'] = request.get_header('Authorization')
            captured['user_agent'] = request.get_header('User-agent')
            captured['payload'] = json.loads(request.data.decode('utf-8'))
            return _FakeResponse()

        with patch('user.email_backends.urllib.request.urlopen', side_effect=fake_urlopen):
            sent = ResendEmailBackend().send_messages([self._message()])

        self.assertEqual(sent, 1)
        self.assertEqual(captured['url'], RESEND_API_URL)
        self.assertTrue(captured['url'].startswith('https://'))
        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(captured['timeout'], 5)
        self.assertEqual(captured['authorization'], 'Bearer re_test_key')
        self.assertEqual(captured['user_agent'], RESEND_USER_AGENT)
        self.assertFalse(captured['user_agent'].lower().startswith('python-urllib'))
        self.assertEqual(captured['payload']['to'], ['buyer@example.com'])
        self.assertEqual(captured['payload']['html'], '<p>Corps HTML</p>')
        self.assertEqual(captured['payload']['text'], 'Corps texte')

    def test_missing_api_key_raises(self):
        with override_settings(RESEND_API_KEY=''):
            with self.assertRaises(OSError):
                ResendEmailBackend().send_messages([self._message()])

    def test_http_error_becomes_oserror(self):
        error = HTTPError(
            RESEND_API_URL,
            401,
            'Unauthorized',
            hdrs=None,
            fp=BytesIO(b'{"message":"invalid"}'),
        )
        with patch('user.email_backends.urllib.request.urlopen', side_effect=error):
            with self.assertRaises(OSError) as caught:
                ResendEmailBackend().send_messages([self._message()])
        self.assertIn('401', str(caught.exception))

    def test_network_error_becomes_oserror(self):
        with patch(
            'user.email_backends.urllib.request.urlopen',
            side_effect=URLError('timed out'),
        ):
            with self.assertRaises(OSError):
                ResendEmailBackend().send_messages([self._message()])
