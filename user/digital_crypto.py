"""
Encryption of OAuth tokens at rest.

`TOKEN_ENCRYPTION_KEY` holds one or more Fernet keys separated by commas: the
first encrypts, all of them decrypt (key rotation). Without a key, nothing is
stored — the integration reports itself as not configured.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


class TokenEncryptionUnavailable(RuntimeError):
    pass


def _raw_keys() -> list[str]:
    raw = getattr(settings, "TOKEN_ENCRYPTION_KEY", None) or os.getenv("TOKEN_ENCRYPTION_KEY", "")
    return [key.strip() for key in raw.split(",") if key.strip()]


def encryption_configured() -> bool:
    try:
        _cipher()
    except TokenEncryptionUnavailable:
        return False
    return True


def _cipher() -> MultiFernet:
    keys = _raw_keys()
    if not keys:
        raise TokenEncryptionUnavailable("TOKEN_ENCRYPTION_KEY is not set")
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (ValueError, TypeError) as exc:
        raise TokenEncryptionUnavailable("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_token(value: str | None) -> str:
    if not value:
        return ""
    return _cipher().encrypt(value.encode()).decode()


def decrypt_token(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenEncryptionUnavailable("Stored token cannot be decrypted with current keys") from exc
