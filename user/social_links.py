"""Paknomik's own social links, editable from the admin space (stored as PlatformSetting)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from user.platform_config import get_setting, set_setting

SOCIAL_SETTING_KEYS = {
    "facebook": "social_facebook_url",
    "instagram": "social_instagram_url",
    "whatsapp": "social_whatsapp_url",
}

_ALLOWED_HOSTS = {
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "instagram": ("instagram.com", "instagr.am"),
    "whatsapp": ("wa.me", "whatsapp.com"),
}

_PHONE = re.compile(r"^\+?[\d\s().-]{8,20}$")


class SocialLinkError(ValueError):
    pass


def normalize_social_url(platform: str, raw: str | None) -> str:
    """Return a safe https URL for the platform, "" for empty input, or raise SocialLinkError."""
    value = (raw or "").strip()
    if not value:
        return ""
    if platform == "whatsapp" and _PHONE.match(value):
        digits = re.sub(r"\D", "", value)
        if not 8 <= len(digits) <= 15:
            raise SocialLinkError("Numéro WhatsApp invalide (indicatif pays inclus, ex. +213…).")
        return f"https://wa.me/{digits}"
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise SocialLinkError("Le lien doit commencer par https://.")
    if not any(host == d or host.endswith(f".{d}") for d in _ALLOWED_HOSTS[platform]):
        raise SocialLinkError(f"Ce lien ne pointe pas vers {', '.join(_ALLOWED_HOSTS[platform])}.")
    if len(value) > 255:
        raise SocialLinkError("Le lien est trop long (255 caractères maximum).")
    return value


def current_social_links() -> dict[str, str]:
    """Stored values, re-validated so an unsafe value saved elsewhere is never served."""
    links = {}
    for platform, key in SOCIAL_SETTING_KEYS.items():
        try:
            links[platform] = normalize_social_url(platform, get_setting(key, ""))
        except SocialLinkError:
            links[platform] = ""
    return links


def save_social_links(values: dict[str, str]) -> dict[str, str]:
    for platform, url in values.items():
        set_setting(SOCIAL_SETTING_KEYS[platform], url)
    return current_social_links()
