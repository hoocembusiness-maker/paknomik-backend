"""
Anonymous visitor context for analytics (geo, session, marketing, device).

Geo sources (first match wins):
  1. Cloudflare headers (CF-IPCountry, CF-IPCity) when behind CF
  2. MaxMind GeoLite2 (.mmdb) when GEOIP2_DATABASE_PATH is set
  3. HTTPS ipwho.is fallback (GEO_LOOKUP_HTTP_FALLBACK, cached 24h)
  4. Local dev: X-Paknomik-Client-Ip or server egress IP when DEBUG

We store a hashed IP (never raw IP in Event.context) and an anonymous session cookie.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import uuid4

from django.conf import settings

import ipaddress

from django.core.cache import cache

from user.geo_ip_resolve import enrich_algeria_wilaya, lookup_geo_for_public_ip
from user.rbac import client_ip

VISITOR_SESSION_COOKIE = "paknomik_sid"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

_UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def visitor_session_id(request) -> str:
    if request is None:
        return ""
    sid = getattr(request, "visitor_session_id", None)
    if sid:
        return str(sid)
    return (request.COOKIES.get(VISITOR_SESSION_COOKIE) or "").strip()


def ensure_visitor_session_id(request) -> str:
    """Return stable session id for this request (middleware sets attribute)."""
    sid = visitor_session_id(request)
    if sid:
        return sid
    sid = str(uuid4())
    request.visitor_session_id = sid
    request._set_visitor_session_cookie = sid
    return sid


def ip_fingerprint(ip: str) -> str:
    if not ip:
        return ""
    pepper = (getattr(settings, "SECRET_KEY", "") or "")[:32]
    digest = hashlib.sha256(f"{pepper}:{ip}".encode()).hexdigest()
    return digest[:24]


def _is_non_public_ip(ip: str) -> bool:
    if not ip:
        return True
    try:
        addr = ipaddress.ip_address(ip.split("%")[0].strip())
    except ValueError:
        return True
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
    )


def _dev_public_ip_header(request) -> str:
    """Trusted only when Django DEBUG and the TCP client is localhost/private."""
    if not getattr(settings, "DEBUG", False) or request is None:
        return ""
    raw = (request.META.get("HTTP_X_PAKNOMIK_CLIENT_IP") or "").strip()
    if not raw or _is_non_public_ip(raw):
        return ""
    return raw[:64]


def _debug_server_public_ip() -> str:
    """Same machine as the browser in local dev — cached egress IP."""
    if not getattr(settings, "DEBUG", False):
        return ""
    cache_key = "paknomik:debug:server_public_ip"
    cached = cache.get(cache_key)
    if isinstance(cached, str) and cached and not _is_non_public_ip(cached):
        return cached
    try:
        import urllib.request

        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as resp:
            ip = resp.read().decode("utf-8").strip()
        if ip and not _is_non_public_ip(ip):
            cache.set(cache_key, ip, 3600)
            return ip
    except OSError:
        pass
    return ""


def ip_for_geo_lookup(request) -> str:
    """Public IP used for geo lookup (dev header / egress when localhost)."""
    ip = client_ip(request)
    if not ip:
        return ""
    if not _is_non_public_ip(ip):
        return ip

    header_ip = _dev_public_ip_header(request)
    if header_ip:
        return header_ip

    if getattr(settings, "DEBUG", False):
        egress = _debug_server_public_ip()
        if egress:
            return egress

    fallback = (
        getattr(settings, "GEOIP2_DEV_FALLBACK_IP", None)
        or __import__("os").environ.get("GEOIP2_DEV_FALLBACK_IP")
        or ""
    ).strip()
    if fallback and getattr(settings, "DEBUG", False):
        return fallback
    return ""


def _cloudflare_geo(request) -> dict[str, str]:
    meta = request.META
    payload: dict[str, str] = {}
    country = (meta.get("HTTP_CF_IPCOUNTRY") or "").strip().upper()
    if country and country != "XX":
        payload["geo_country"] = country
    city = (meta.get("HTTP_CF_IPCITY") or "").strip()
    if city:
        payload["geo_city"] = city
    region = (meta.get("HTTP_CF_REGION") or meta.get("HTTP_CF_REGION_CODE") or "").strip()
    if region:
        payload["geo_region"] = region
    return payload


def lookup_geo_from_request(request) -> dict[str, str]:
    if request is None:
        return {}
    cf = _cloudflare_geo(request)
    if cf.get("geo_country"):
        enrich_algeria_wilaya(cf)
        cf.setdefault("geo_source", "cloudflare")
        return cf
    ip = ip_for_geo_lookup(request)
    if not ip:
        return cf
    geo = lookup_geo_for_public_ip(ip)
    if geo:
        return geo
    return cf


#: Lowercase user-agent fragments of crawlers, scripts, monitors and link previews.
BOT_USER_AGENT_MARKERS = (
    "bot", "spider", "crawl", "slurp", "preview", "facebookexternalhit", "whatsapp/",
    "embedly", "vkshare", "skypeuripreview", "curl/", "wget/", "python-requests",
    "python-urllib", "httpx", "aiohttp", "go-http-client", "java/", "okhttp", "axios/",
    "node-fetch", "undici", "postman", "insomnia", "scrapy", "headless", "phantomjs",
    "lighthouse", "pagespeed", "pingdom", "uptimerobot", "statuscake", "monitor",
    "datadog", "newrelic", "site24x7", "checkly",
)


def is_bot_user_agent(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    return any(marker in ua for marker in BOT_USER_AGENT_MARKERS)


def _device_class(user_agent: str) -> str:
    ua = user_agent.lower()
    if is_bot_user_agent(ua):
        return "bot"
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    if "tablet" in ua or "ipad" in ua:
        return "tablet"
    return "desktop"


def _browser_family(user_agent: str) -> str:
    ua = user_agent
    for pattern, name in (
        (r"Edg/", "edge"),
        (r"Chrome/", "chrome"),
        (r"Firefox/", "firefox"),
        (r"Safari/", "safari"),
    ):
        if re.search(pattern, ua):
            return name
    return "other"


def request_analytics_context(request) -> dict[str, Any]:
    """Enrichment merged into Event.context for visitors and logged-in users."""
    if request is None:
        return {}

    payload: dict[str, Any] = {}
    sid = ensure_visitor_session_id(request)
    if sid:
        payload["visitor_session_id"] = sid

    ip = client_ip(request)
    ip_hash = ip_fingerprint(ip)
    if ip_hash:
        payload["geo_ip_hash"] = ip_hash

    payload.update(lookup_geo_from_request(request))

    ua = (request.META.get("HTTP_USER_AGENT") or "")[:512]
    if ua:
        payload["user_agent"] = ua
        payload["device_class"] = _device_class(ua)
        payload["browser_family"] = _browser_family(ua)

    lang = (request.META.get("HTTP_ACCEPT_LANGUAGE") or "").split(",")[0].strip()[:32]
    if lang:
        payload["accept_language"] = lang

    referrer = (request.META.get("HTTP_REFERER") or "")[:512]
    if referrer:
        payload["referrer"] = referrer

    for key in _UTM_KEYS:
        value = request.GET.get(key) or request.POST.get(key)
        if value:
            payload[key] = str(value)[:200]

    path = getattr(request, "path", "") or ""
    if path:
        payload["request_path"] = path[:255]

    return payload


def actor_dedupe_key(request, user) -> str:
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.id}"
    sid = visitor_session_id(request)
    if sid:
        return f"sid:{sid}"
    ip = client_ip(request) if request else ""
    return f"ip:{ip_fingerprint(ip) or 'anon'}"
