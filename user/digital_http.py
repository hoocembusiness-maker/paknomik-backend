"""
Minimal JSON HTTP client for the digital presence providers.

- Never logs URLs, params or bodies (they may carry tokens).
- Retries transient failures (network, 429, 5xx) with a short backoff.
- Normalizes provider errors into `ProviderError` codes with token-free messages.
The transport is swappable so tests never reach the network.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from typing import Callable
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlencode

DEFAULT_TIMEOUT = 20

ERROR_MESSAGES = {
    "not_configured": "Cette intégration n'est pas encore configurée sur le serveur Paknomik.",
    "token_expired": "L'autorisation a expiré ou a été révoquée : reconnectez le compte.",
    "insufficient_scope": "Les autorisations accordées sont insuffisantes : reconnectez en acceptant l'accès en lecture demandé.",
    "insufficient_permission": "Le compte connecté n'a pas les droits nécessaires sur cette ressource.",
    "ineligible_account": "Ce compte n'est pas admissible aux statistiques.",
    "insights_unavailable": "Les statistiques ne sont pas encore disponibles pour cette ressource.",
    "resource_inaccessible": "Cette ressource n'est pas accessible avec le compte autorisé : choisissez-en une autre.",
    "unsupported_metric": "Métrique non prise en charge par la source pour cette ressource.",
    "rate_limited": "La source limite temporairement les requêtes ; une nouvelle tentative aura lieu plus tard.",
    "invalid_request": "La source a refusé la requête.",
    "network_error": "La source est momentanément injoignable.",
    "provider_error": "La source a renvoyé une erreur inattendue.",
    "decrypt_failed": "Le jeton stocké ne peut plus être déchiffré : reconnectez le compte.",
}

REAUTH_CODES = {"token_expired", "insufficient_scope", "decrypt_failed"}


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status: int | None = None,
        provider_code: str = "",
        retryable: bool = False,
    ):
        self.code = code
        self.status = status
        self.provider_code = str(provider_code or "")
        self.retryable = retryable
        self.message = scrub(message or ERROR_MESSAGES.get(code, ERROR_MESSAGES["provider_error"]))
        super().__init__(f"{code}: {self.message}")

    @property
    def needs_reauth(self) -> bool:
        return self.code in REAUTH_CODES

    @property
    def user_message(self) -> str:
        return ERROR_MESSAGES.get(self.code, ERROR_MESSAGES["provider_error"])


_TOKEN_PATTERNS = [
    re.compile(r"(access_token|refresh_token|client_secret|code|fb_exchange_token)=([^&\s\"']+)", re.I),
    re.compile(r"(\"(?:access_token|refresh_token|id_token|client_secret)\"\s*:\s*\")([^\"]+)", re.I),
    re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.I),
]
_LONG_SECRET = re.compile(r"\b[A-Za-z0-9_\-\.]{40,}\b")


def scrub(text: str) -> str:
    """Remove anything that looks like a token or secret from a message."""
    if not text:
        return ""
    cleaned = str(text)
    for pattern in _TOKEN_PATTERNS:
        cleaned = pattern.sub(lambda m: f"{m.group(1)}[masqué]", cleaned)
    cleaned = _LONG_SECRET.sub("[masqué]", cleaned)
    return cleaned[:480]


Transport = Callable[[str, str, dict, bytes | None, int], tuple[int, bytes]]


def _urllib_transport(method, url, headers, body, timeout):
    req = urlrequest.Request(url, data=body, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            return resp.status, resp.read()
    except urlerror.HTTPError as exc:
        return exc.code, exc.read() or b""


_transport: Transport = _urllib_transport
_sleep: Callable[[float], None] = time.sleep


@contextmanager
def use_transport(transport: Transport, *, sleep: Callable[[float], None] | None = None):
    """Swap the HTTP transport (tests, demo tooling)."""
    global _transport, _sleep
    previous, previous_sleep = _transport, _sleep
    _transport = transport
    _sleep = sleep or (lambda _seconds: None)
    try:
        yield
    finally:
        _transport, _sleep = previous, previous_sleep


def classify_error(provider: str, status: int, payload) -> ProviderError:
    if provider in ("meta", "instagram"):
        err = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(err, dict):
            err = {}
        code = int(err.get("code") or 0)
        subcode = err.get("error_subcode")
        message = err.get("message") or ""
        lowered = message.lower()
        if code in (190, 102):
            return ProviderError("token_expired", status=status, provider_code=code)
        if code in (4, 17, 32, 613) or 80000 <= code < 80100:
            return ProviderError("rate_limited", status=status, provider_code=code, retryable=True)
        if code == 10 or 200 <= code < 300:
            return ProviderError(
                "insufficient_permission", message, status=status, provider_code=code
            )
        if code == 100 and "metric" in lowered:
            return ProviderError("unsupported_metric", message, status=status, provider_code=code)
        if code in (100, 3001) and subcode in (2108006, 1504028):
            return ProviderError("insights_unavailable", message, status=status, provider_code=code)
        if code == 100:
            return ProviderError("invalid_request", message, status=status, provider_code=code)
        if status >= 500 or err.get("is_transient"):
            return ProviderError("provider_error", message, status=status, retryable=True)
        return ProviderError("provider_error", message, status=status, provider_code=code)

    # Google (OAuth endpoint uses {"error": "invalid_grant"}, APIs use {"error": {...}}).
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, str):
        if err in ("invalid_grant", "unauthorized_client", "invalid_token"):
            return ProviderError("token_expired", status=status, provider_code=err)
        if err == "invalid_scope":
            return ProviderError("insufficient_scope", status=status, provider_code=err)
        return ProviderError("provider_error", payload.get("error_description", ""), status=status)
    err = err if isinstance(err, dict) else {}
    google_status = err.get("status") or ""
    message = err.get("message") or ""
    reasons = {
        str(detail.get("reason") or "")
        for detail in (err.get("details") or []) + (err.get("errors") or [])
        if isinstance(detail, dict)
    }
    if status == 401 or google_status == "UNAUTHENTICATED":
        return ProviderError("token_expired", status=status, provider_code=google_status)
    if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in reasons or "insufficientPermissions" in reasons:
        return ProviderError("insufficient_scope", status=status, provider_code=google_status)
    if status == 429 or google_status == "RESOURCE_EXHAUSTED":
        return ProviderError("rate_limited", status=status, provider_code=google_status, retryable=True)
    if status == 403 or google_status == "PERMISSION_DENIED":
        return ProviderError("resource_inaccessible", message, status=status, provider_code=google_status)
    if status == 404:
        return ProviderError("resource_inaccessible", message, status=status, provider_code="NOT_FOUND")
    if status >= 500:
        return ProviderError("provider_error", message, status=status, retryable=True)
    return ProviderError("invalid_request", message, status=status, provider_code=google_status)


def request_json(
    method: str,
    url: str,
    *,
    provider: str,
    params: dict | None = None,
    form: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    retries: int = 2,
    timeout: int = DEFAULT_TIMEOUT,
):
    full_url = url
    if params:
        full_url = f"{url}{'&' if '?' in url else '?'}{urlencode(params, doseq=True)}"
    send_headers = {"Accept": "application/json", **(headers or {})}
    body = None
    if form is not None:
        body = urlencode(form).encode()
        send_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        body = json.dumps(json_body).encode()
        send_headers["Content-Type"] = "application/json"

    attempt = 0
    while True:
        attempt += 1
        try:
            status, raw = _transport(method, full_url, send_headers, body, timeout)
        except (urlerror.URLError, TimeoutError, OSError):
            if attempt <= retries:
                _sleep(min(2**attempt, 8))
                continue
            raise ProviderError("network_error", retryable=True) from None
        try:
            payload = json.loads(raw.decode() or "{}") if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {}
        if 200 <= status < 300:
            return payload
        error = classify_error(provider, status, payload)
        if error.retryable and attempt <= retries:
            _sleep(min(2**attempt, 8))
            continue
        raise error
