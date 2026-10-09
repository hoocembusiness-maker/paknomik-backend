"""
Resolve country / region / city for a public IP.

Priority for Paknomik:
  1. Cloudflare request headers (handled in visitor_tracking)
  2. Local MaxMind GeoLite2 (.mmdb) when configured — fast, no quota
  3. HTTPS ipwho.is fallback when the DB misses or is absent — good for dev / edge cases

Production tip: put the site behind Cloudflare for the most stable visitor geo.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_GEO_CACHE_SECONDS = 60 * 60 * 24


def _geo_cache_key(ip: str) -> str:
    return f"paknomik:geo:ip:v1:{ip}"


def _maxmind_lookup(ip: str) -> dict[str, str]:
    db_path = (
        getattr(settings, "GEOIP2_DATABASE_PATH", None)
        or __import__("os").environ.get("GEOIP2_DATABASE_PATH")
    )
    if not db_path or not Path(db_path).is_file():
        return {}
    try:
        import geoip2.database
        import geoip2.errors
    except ImportError:
        return {}
    try:
        with geoip2.database.Reader(db_path) as reader:
            record = reader.city(ip)
    except (geoip2.errors.AddressNotFoundError, ValueError, OSError):
        return {}

    payload: dict[str, str] = {}
    if record.country and record.country.iso_code:
        payload["geo_country"] = record.country.iso_code.upper()
    if record.country and record.country.name:
        payload["geo_country_name"] = record.country.name
    if record.subdivisions:
        payload["geo_region"] = record.subdivisions[0].iso_code or ""
        if record.subdivisions[0].name:
            payload["geo_region_name"] = record.subdivisions[0].name
    if record.city and record.city.name:
        payload["geo_city"] = record.city.name
    if record.location and record.location.time_zone:
        payload["geo_timezone"] = record.location.time_zone
    if payload:
        payload["geo_source"] = "maxmind"
    return payload


def _http_lookup(ip: str) -> dict[str, str]:
    if not getattr(settings, "GEO_LOOKUP_HTTP_FALLBACK", True):
        return {}
    url = f"https://ipwho.is/{ip}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("HTTP geo lookup failed for %s: %s", ip, exc)
        return {}

    if not data.get("success"):
        return {}

    payload: dict[str, str] = {}
    code = (data.get("country_code") or "").strip().upper()
    if code:
        payload["geo_country"] = code
    country = (data.get("country") or "").strip()
    if country:
        payload["geo_country_name"] = country
    region = (data.get("region") or "").strip()
    if region:
        payload["geo_region_name"] = region
    city = (data.get("city") or "").strip()
    if city:
        payload["geo_city"] = city
    tz = (data.get("timezone", {}) or {}).get("id") if isinstance(data.get("timezone"), dict) else data.get("timezone")
    if tz:
        payload["geo_timezone"] = str(tz)
    if payload:
        payload["geo_source"] = "ipwho"
    return payload


def enrich_algeria_wilaya(payload: dict[str, str]) -> None:
    if payload.get("geo_country") != "DZ":
        return
    from user.models import Supplier

    region = (payload.get("geo_region_name") or "").strip().lower()
    city = (payload.get("geo_city") or "").strip().lower()
    aliases = {"algiers": "algiers", "alger": "algiers"}
    region_key = aliases.get(region, region.replace(" ", "_"))

    for code, label in Supplier.WILAYA_CHOICES:
        lab = label.lower()
        code_norm = code.replace("_", " ")
        if region and (lab == region or code == region_key or code_norm == region):
            payload["geo_wilaya"] = code
            return
        if city and (lab == city or code.replace("_", " ") == city):
            payload["geo_wilaya"] = code
            return


def lookup_geo_for_public_ip(ip: str) -> dict[str, str]:
    if not ip:
        return {}

    cached = cache.get(_geo_cache_key(ip))
    if isinstance(cached, dict) and cached:
        return dict(cached)

    payload = _maxmind_lookup(ip)
    if not payload.get("geo_country"):
        payload = _http_lookup(ip)
    enrich_algeria_wilaya(payload)
    if payload:
        cache.set(_geo_cache_key(ip), payload, _GEO_CACHE_SECONDS)
    return payload
