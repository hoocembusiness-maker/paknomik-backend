"""HTTPS email backends. Railway Hobby blocks outbound SMTP."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


RESEND_API_URL = 'https://api.resend.com/emails'
RESEND_USER_AGENT = 'PaknomikBackend/1.0'


class ResendEmailBackend(BaseEmailBackend):
    """Send mail through Resend's HTTPS API (port 443)."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        api_key = (getattr(settings, 'RESEND_API_KEY', '') or '').strip()
        if not api_key:
            raise OSError('RESEND_API_KEY is not configured')
        sent = 0
        for message in email_messages:
            try:
                self._send(message, api_key)
            except Exception:
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent

    def _send(self, message, api_key: str) -> None:
        html = _html_alternative(message)
        payload = {
            'from': message.from_email,
            'to': list(message.to),
            'subject': message.subject,
            'text': message.body or '',
        }
        if html:
            payload['html'] = html
        if message.cc:
            payload['cc'] = list(message.cc)
        if message.bcc:
            payload['bcc'] = list(message.bcc)
        if message.reply_to:
            payload['reply_to'] = list(message.reply_to)

        timeout = int(getattr(settings, 'EMAIL_TIMEOUT', 20) or 20)
        request = urllib.request.Request(
            RESEND_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': RESEND_USER_AGENT,
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status >= 400:
                    raise OSError(f'Resend HTTP {response.status}')
        except urllib.error.HTTPError as exc:
            detail = _safe_http_detail(exc)
            raise OSError(f'Resend HTTP {exc.code}{detail}') from exc
        except urllib.error.URLError as exc:
            raise OSError(f'Resend request failed: {exc.reason}') from exc


def _html_alternative(message) -> str:
    for content, mimetype in getattr(message, 'alternatives', None) or ():
        if mimetype == 'text/html' and content:
            return content
    return ''


def _safe_http_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode('utf-8', errors='replace')[:180]
    except OSError:
        return ''
    if not body:
        return ''
    return f': {body}'
