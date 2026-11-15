"""Verify Google ID tokens with a cached cert fetch.

django-allauth's jwtkit.fetch_key hits Google certs on every login with no
cache and no timeout, which makes GIS sign-in feel slow (especially new
signups that also create the SocialAccount).
"""

from __future__ import annotations

import logging

import jwt
from django.core.cache import cache

from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.internal.jwtkit import (
    lookup_kid_pem_x509_certificate,
    verify_jti,
)
from allauth.socialaccount.providers.oauth2.client import OAuth2Error

logger = logging.getLogger(__name__)

GOOGLE_CERTS_URL = 'https://www.googleapis.com/oauth2/v1/certs'
GOOGLE_ISSUERS = ('https://accounts.google.com', 'accounts.google.com')
CERTS_CACHE_KEY = 'paknomik:google_oauth_certs'
CERTS_CACHE_TTL = 60 * 60  # 1 hour — Google rotates keys infrequently
CERTS_FETCH_TIMEOUT = 5


def _fetch_certs(*, force_refresh: bool = False) -> dict:
    if not force_refresh:
        cached = cache.get(CERTS_CACHE_KEY)
        if isinstance(cached, dict) and cached:
            return cached

    with get_adapter().get_requests_session() as sess:
        response = sess.get(GOOGLE_CERTS_URL, timeout=CERTS_FETCH_TIMEOUT)
        response.raise_for_status()
        certs = response.json()

    if not isinstance(certs, dict) or not certs:
        raise OAuth2Error('Google certs response was empty')

    cache.set(CERTS_CACHE_KEY, certs, timeout=CERTS_CACHE_TTL)
    return certs


def _public_key_for_credential(credential: str):
    header = jwt.get_unverified_header(credential)
    kid = header.get('kid')
    alg = header.get('alg')
    if not kid or not alg:
        raise OAuth2Error("Invalid Google ID token header")

    certs = _fetch_certs()
    key = lookup_kid_pem_x509_certificate(certs, kid)
    if key is None:
        # Key rotation: refresh once, then fail.
        certs = _fetch_certs(force_refresh=True)
        key = lookup_kid_pem_x509_certificate(certs, kid)
    if key is None:
        raise OAuth2Error(f"Invalid 'kid': '{kid}'")
    return alg, key


def verify_google_id_token(credential: str, audience: str) -> dict:
    """Decode and verify a Google GIS ID token; returns the claims dict."""
    if not credential or not audience:
        raise OAuth2Error('Invalid id_token')

    try:
        alg, key = _public_key_for_credential(credential)
        data = jwt.decode(
            credential,
            key=key,
            options={
                'verify_signature': True,
                'verify_iss': True,
                'verify_aud': True,
                'verify_exp': True,
            },
            issuer=list(GOOGLE_ISSUERS),
            audience=audience,
            algorithms=[alg],
        )
        verify_jti(data)
        return data
    except jwt.PyJWTError as exc:
        raise OAuth2Error('Invalid id_token') from exc
