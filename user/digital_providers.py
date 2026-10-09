"""
Provider clients: OAuth (authorize URL, code exchange, refresh, revoke),
resource discovery and metric collection for Meta Pages, Instagram Login,
GA4 Data API and Search Console.

Functions return plain dicts — no Django model writes happen here, so the
sync layer stays the single place that persists data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

from django.utils import timezone

from user.digital_config import (
    GA4_ADMIN_API,
    GA4_DATA_API,
    META_GRAPH_API_VERSION,
    METRICS,
    RECENT_CONTENT_LIMIT,
    SEARCH_CONSOLE_API,
    TOP_DIMENSIONS,
    TOP_ROWS,
    WINDOWS,
    ProviderConfig,
)
from user.digital_http import ProviderError, request_json
from user.digital_models import DigitalPlatform

GRAPH_FB = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"
GRAPH_IG = f"https://graph.instagram.com/{META_GRAPH_API_VERSION}"

#: Per-metric failures that only make *that* metric unavailable.
METRIC_LEVEL_ERRORS = {
    "unsupported_metric",
    "insufficient_permission",
    "insights_unavailable",
    "invalid_request",
}


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str = ""
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    account_id: str = ""
    account_name: str = ""


@dataclass
class FetchResult:
    records: list[dict] = field(default_factory=list)
    unsupported: dict[str, str] = field(default_factory=dict)
    supported: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    source_timezone: str = ""


# ── helpers ─────────────────────────────────────────────────────────────────


def _expires_at(seconds) -> datetime | None:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return timezone.now() + timedelta(seconds=seconds)


def to_decimal(value) -> Decimal | None:
    """Numeric value or None. Breakdown dicts are summed; absence stays None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        parts = [to_decimal(v) for v in value.values()]
        parts = [p for p in parts if p is not None]
        return sum(parts, Decimal("0")) if parts else None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def record(
    metric_key,
    value,
    unit,
    period_start,
    period_end,
    granularity,
    *,
    tz="",
    dimension_type="",
    dimension_value="",
    dimension_label="",
    provenance=None,
):
    return {
        "metric_key": metric_key,
        "value": to_decimal(value),
        "unit": unit,
        "period_start": period_start,
        "period_end": period_end,
        "granularity": granularity,
        "source_timezone": tz,
        "dimension_type": dimension_type,
        "dimension_value": str(dimension_value or ""),
        "dimension_label": str(dimension_label or "")[:500],
        "provenance": provenance or {},
    }


def _chunks(start: date, end: date, size: int):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=size - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _unix(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=dt_timezone.utc).timestamp())


def _meta_day_from_end_time(end_time: str) -> date | None:
    """Meta stamps a daily value with the *end* of the day (next day)."""
    try:
        return date.fromisoformat(end_time[:10]) - timedelta(days=1)
    except (TypeError, ValueError):
        return None


def _windows(end: date):
    for days in WINDOWS:
        yield days, end - timedelta(days=days - 1), end


def _skip(platform, key, skip_metrics):
    return key in (skip_metrics or set())


# ── OAuth ───────────────────────────────────────────────────────────────────


def build_authorization_url(config: ProviderConfig, *, state: str, code_challenge: str | None):
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": (" " if config.provider == "google" else ",").join(config.scopes),
        **config.extra_auth_params,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{config.authorize_url}?{urlencode(params)}"


def exchange_code(config: ProviderConfig, *, code: str, code_verifier: str = "") -> TokenBundle:
    if config.provider == "google":
        form = {
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        payload = request_json("POST", config.token_url, provider="google", form=form, retries=0)
        return TokenBundle(
            access_token=payload.get("access_token", ""),
            refresh_token=payload.get("refresh_token", ""),
            expires_at=_expires_at(payload.get("expires_in")),
            scopes=(payload.get("scope") or "").split(),
            account_name="Compte Google autorisé",
        )

    if config.provider == "meta":
        params = {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "code": code,
        }
        if code_verifier:
            params["code_verifier"] = code_verifier
        short = request_json("GET", config.token_url, provider="meta", params=params, retries=0)
        long_lived = request_json(
            "GET",
            config.token_url,
            provider="meta",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "fb_exchange_token": short.get("access_token", ""),
            },
            retries=0,
        )
        token = long_lived.get("access_token") or short.get("access_token", "")
        me = request_json(
            "GET", f"{GRAPH_FB}/me", provider="meta", params={"fields": "id,name", "access_token": token}
        )
        perms = request_json(
            "GET", f"{GRAPH_FB}/me/permissions", provider="meta", params={"access_token": token}
        )
        granted = [
            row.get("permission")
            for row in perms.get("data", [])
            if row.get("status") == "granted" and row.get("permission")
        ]
        return TokenBundle(
            access_token=token,
            expires_at=_expires_at(long_lived.get("expires_in")),
            scopes=granted,
            account_id=str(me.get("id") or ""),
            account_name=me.get("name") or "",
        )

    # Instagram API with Instagram Login (graph.instagram.com, IG user token).
    payload = request_json(
        "POST",
        config.token_url,
        provider="instagram",
        form={
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "grant_type": "authorization_code",
            "redirect_uri": config.redirect_uri,
            "code": code,
        },
        retries=0,
    )
    if isinstance(payload.get("data"), list) and payload["data"]:
        payload = payload["data"][0]
    permissions = payload.get("permissions") or []
    if isinstance(permissions, str):
        permissions = [p.strip() for p in permissions.split(",") if p.strip()]
    long_lived = request_json(
        "GET",
        "https://graph.instagram.com/access_token",
        provider="instagram",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": config.client_secret,
            "access_token": payload.get("access_token", ""),
        },
        retries=0,
    )
    return TokenBundle(
        access_token=long_lived.get("access_token") or payload.get("access_token", ""),
        expires_at=_expires_at(long_lived.get("expires_in")),
        scopes=list(permissions),
        account_id=str(payload.get("user_id") or ""),
    )


def refresh_access_token(config: ProviderConfig, *, access_token: str, refresh_token: str):
    """Return a fresh TokenBundle, or None when the provider has no refresh path."""
    if config.provider == "google":
        if not refresh_token:
            raise ProviderError("token_expired")
        payload = request_json(
            "POST",
            config.token_url,
            provider="google",
            form={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        return TokenBundle(
            access_token=payload.get("access_token", ""),
            refresh_token=payload.get("refresh_token", "") or refresh_token,
            expires_at=_expires_at(payload.get("expires_in")),
            scopes=(payload.get("scope") or "").split(),
        )
    if config.provider == "instagram":
        payload = request_json(
            "GET",
            "https://graph.instagram.com/refresh_access_token",
            provider="instagram",
            params={"grant_type": "ig_refresh_token", "access_token": access_token},
        )
        return TokenBundle(
            access_token=payload.get("access_token", "") or access_token,
            expires_at=_expires_at(payload.get("expires_in")),
        )
    # Facebook long-lived user tokens cannot be refreshed without the user;
    # Page tokens derived from them do not expire.
    return None


def revoke_grant(config: ProviderConfig, *, access_token: str, refresh_token: str) -> bool:
    """Best-effort revocation at the provider. Never raises."""
    try:
        if config.provider == "google":
            token = refresh_token or access_token
            if token:
                request_json(
                    "POST",
                    "https://oauth2.googleapis.com/revoke",
                    provider="google",
                    form={"token": token},
                    retries=0,
                )
                return True
        elif config.provider == "meta" and access_token:
            request_json(
                "DELETE",
                f"{GRAPH_FB}/me/permissions",
                provider="meta",
                params={"access_token": access_token},
                retries=0,
            )
            return True
    except ProviderError:
        return False
    return False


def missing_scopes(config: ProviderConfig, granted: list[str]) -> list[str]:
    """Required scopes the user declined. Google may echo scopes in any order."""
    if not granted:
        # Instagram omits the list on some API versions; insights calls will tell.
        return [] if config.provider == "instagram" else list(config.scopes)
    granted_set = set(granted)
    return [scope for scope in config.scopes if scope not in granted_set]


# ── Resource discovery ──────────────────────────────────────────────────────


def _paginate_meta(url, params, provider="meta", max_pages=20):
    items = []
    next_url, next_params = url, dict(params)
    for _ in range(max_pages):
        payload = request_json("GET", next_url, provider=provider, params=next_params)
        items.extend(payload.get("data", []))
        paging_next = (payload.get("paging") or {}).get("next")
        if not paging_next:
            break
        next_url, next_params = paging_next, None
    return items


def list_resources(platform: str, *, access_token: str) -> tuple[list[dict], dict[str, str]]:
    """Return (public resources, {resource_id: resource_token}) for the grant.

    Resource tokens (Facebook Page tokens) are returned separately so they are
    never stored in the public `available_resources` JSON.
    """
    if platform == DigitalPlatform.META_FACEBOOK:
        pages = _paginate_meta(
            f"{GRAPH_FB}/me/accounts",
            {"fields": "id,name,tasks,access_token", "limit": 100, "access_token": access_token},
        )
        resources, tokens = [], {}
        for page in pages:
            page_id = str(page.get("id") or "")
            if not page_id:
                continue
            tasks = page.get("tasks") or []
            eligible = "ANALYZE" in tasks
            resources.append(
                {
                    "id": page_id,
                    "name": page.get("name") or page_id,
                    "resourceType": "facebook_page",
                    "eligible": eligible,
                    "reason": ""
                    if eligible
                    else "Votre rôle sur cette Page ne permet pas de consulter les statistiques (tâche ANALYZE requise).",
                }
            )
            if page.get("access_token"):
                tokens[page_id] = page["access_token"]
        return resources, tokens

    if platform == DigitalPlatform.META_INSTAGRAM:
        me = request_json(
            "GET",
            f"{GRAPH_IG}/me",
            provider="instagram",
            params={"fields": "user_id,username,name,account_type", "access_token": access_token},
        )
        account_type = (me.get("account_type") or "").upper()
        eligible = account_type in ("BUSINESS", "MEDIA_CREATOR")
        resource_id = str(me.get("user_id") or me.get("id") or "")
        return (
            [
                {
                    "id": resource_id,
                    "name": f"@{me.get('username')}" if me.get("username") else resource_id,
                    "resourceType": "instagram_account",
                    "accountType": account_type,
                    "eligible": eligible,
                    "reason": ""
                    if eligible
                    else "Compte Instagram personnel : passez à un compte professionnel (Business ou Créateur) pour accéder aux statistiques.",
                }
            ]
            if resource_id
            else [],
            {},
        )

    headers = {"Authorization": f"Bearer {access_token}"}
    if platform == DigitalPlatform.GOOGLE_ANALYTICS:
        resources, page_token = [], None
        for _ in range(20):
            params = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            payload = request_json(
                "GET", f"{GA4_ADMIN_API}/accountSummaries", provider="google", params=params, headers=headers
            )
            for account in payload.get("accountSummaries", []):
                for prop in account.get("propertySummaries", []):
                    resources.append(
                        {
                            "id": prop.get("property", ""),
                            "name": prop.get("displayName") or prop.get("property", ""),
                            "account": account.get("displayName", ""),
                            "resourceType": "ga4_property",
                            "eligible": True,
                            "reason": "",
                        }
                    )
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return [r for r in resources if r["id"]], {}

    payload = request_json("GET", f"{SEARCH_CONSOLE_API}/sites", provider="google", headers=headers)
    resources = []
    for site in payload.get("siteEntry", []):
        level = site.get("permissionLevel", "")
        eligible = level != "siteUnverifiedUser"
        resources.append(
            {
                "id": site.get("siteUrl", ""),
                "name": site.get("siteUrl", ""),
                "permissionLevel": level,
                "resourceType": "search_console_site",
                "eligible": eligible,
                "reason": ""
                if eligible
                else "Ce compte Google n'est pas un utilisateur vérifié de cette propriété Search Console.",
            }
        )
    return [r for r in resources if r["id"]], {}


# ── Metrics: Facebook Page ──────────────────────────────────────────────────


def _fb_insights(page_id, token, params):
    payload = request_json(
        "GET",
        f"{GRAPH_FB}/{page_id}/insights",
        provider="meta",
        params={**params, "access_token": token},
    )
    return payload.get("data", [])


def fetch_facebook(page_id, *, token, start, end, skip_metrics=None) -> FetchResult:
    platform = DigitalPlatform.META_FACEBOOK
    tz = "America/Los_Angeles"
    result = FetchResult(source_timezone=tz)
    any_value = False

    for spec in METRICS[platform]:
        if _skip(platform, spec.key, skip_metrics):
            continue
        try:
            if spec.aggregation == "sum":
                for chunk_start, chunk_end in _chunks(start, end, 90):
                    rows = _fb_insights(
                        page_id,
                        token,
                        {
                            "metric": spec.api_name,
                            "period": "day",
                            "since": chunk_start.isoformat(),
                            "until": (chunk_end + timedelta(days=1)).isoformat(),
                        },
                    )
                    for row in rows:
                        for point in row.get("values", []):
                            day = _meta_day_from_end_time(point.get("end_time", ""))
                            if day is None or day < chunk_start or day > chunk_end:
                                continue
                            rec = record(spec.key, point.get("value"), spec.unit, day, day, "day", tz=tz)
                            any_value = any_value or rec["value"] is not None
                            result.records.append(rec)
            else:
                # Unique viewers: only the source's own week / 28-day values.
                for days, period in ((7, "week"), (28, "days_28")):
                    rows = _fb_insights(
                        page_id,
                        token,
                        {
                            "metric": spec.api_name,
                            "period": period,
                            "since": end.isoformat(),
                            "until": (end + timedelta(days=1)).isoformat(),
                        },
                    )
                    for row in rows:
                        for point in row.get("values", []):
                            if _meta_day_from_end_time(point.get("end_time", "")) != end:
                                continue
                            rec = record(
                                spec.key,
                                point.get("value"),
                                spec.unit,
                                end - timedelta(days=days - 1),
                                end,
                                "window",
                                tz=tz,
                                provenance={"period": period},
                            )
                            any_value = any_value or rec["value"] is not None
                            result.records.append(rec)
            result.supported.add(spec.key)
        except ProviderError as exc:
            if exc.code not in METRIC_LEVEL_ERRORS:
                raise
            result.unsupported[spec.key] = exc.code

    if not any_value and not result.unsupported:
        result.notes.append("insights_unavailable")

    try:
        posts = request_json(
            "GET",
            f"{GRAPH_FB}/{page_id}/posts",
            provider="meta",
            params={
                "fields": "id,message,created_time,permalink_url",
                "since": (end - timedelta(days=89)).isoformat(),
                "limit": RECENT_CONTENT_LIMIT,
                "access_token": token,
            },
        ).get("data", [])
    except ProviderError as exc:
        if exc.code not in METRIC_LEVEL_ERRORS:
            raise
        posts = []
        result.notes.append("content_unavailable")

    for post in posts[:RECENT_CONTENT_LIMIT]:
        post_id = post.get("id")
        published = (post.get("created_time") or "")[:10]
        try:
            published_day = date.fromisoformat(published)
        except ValueError:
            continue
        label = (post.get("message") or "").strip().replace("\n", " ")[:140] or f"Publication du {published}"
        values = {}
        for metric in ("post_media_view", "post_total_media_view_unique", "post_reactions_by_type_total"):
            try:
                rows = request_json(
                    "GET",
                    f"{GRAPH_FB}/{post_id}/insights",
                    provider="meta",
                    params={"metric": metric, "access_token": token},
                ).get("data", [])
            except ProviderError as exc:
                if exc.code not in METRIC_LEVEL_ERRORS:
                    raise
                continue
            for row in rows:
                points = row.get("values") or []
                if points:
                    values[metric] = points[0].get("value")
        for metric, value in values.items():
            result.records.append(
                record(
                    metric,
                    value,
                    "count",
                    published_day,
                    published_day,
                    "lifetime",
                    tz=tz,
                    dimension_type="content",
                    dimension_value=post_id,
                    dimension_label=label,
                    provenance={"permalink": post.get("permalink_url", "")},
                )
            )
    return result


# ── Metrics: Instagram (Instagram Login) ────────────────────────────────────

IG_ADDITIVE = {"views", "total_interactions", "profile_views"}


def _ig_insights(ig_id, token, params):
    payload = request_json(
        "GET",
        f"{GRAPH_IG}/{ig_id}/insights",
        provider="instagram",
        params={**params, "access_token": token},
    )
    return payload.get("data", [])


def _ig_total(ig_id, token, metric, start, end):
    rows = _ig_insights(
        ig_id,
        token,
        {
            "metric": metric,
            "period": "day",
            "metric_type": "total_value",
            "since": _unix(start),
            "until": _unix(end + timedelta(days=1)),
        },
    )
    for row in rows:
        total = row.get("total_value") or {}
        if "value" in total:
            return total.get("value")
    return None


def fetch_instagram(ig_id, *, token, start, end, skip_metrics=None) -> FetchResult:
    platform = DigitalPlatform.META_INSTAGRAM
    result = FetchResult()

    for spec in METRICS[platform]:
        if _skip(platform, spec.key, skip_metrics):
            continue
        try:
            if spec.chart:
                for chunk_start, chunk_end in _chunks(start, end, 30):
                    rows = _ig_insights(
                        ig_id,
                        token,
                        {
                            "metric": spec.api_name,
                            "period": "day",
                            "metric_type": "time_series",
                            "since": _unix(chunk_start),
                            "until": _unix(chunk_end + timedelta(days=1)),
                        },
                    )
                    for row in rows:
                        for point in row.get("values", []):
                            day = _meta_day_from_end_time(point.get("end_time", ""))
                            if day is None or day < chunk_start or day > chunk_end:
                                continue
                            result.records.append(
                                record(spec.key, point.get("value"), spec.unit, day, day, "day")
                            )
            for days, window_start, window_end in _windows(end):
                if days <= 30:
                    value = _ig_total(ig_id, token, spec.api_name, window_start, window_end)
                    provenance = {"metric_type": "total_value"}
                elif spec.key in IG_ADDITIVE:
                    # Meta caps total_value ranges at 30 days: add exact 30-day blocks.
                    parts = [
                        _ig_total(ig_id, token, spec.api_name, s, e)
                        for s, e in _chunks(window_start, window_end, 30)
                    ]
                    value = None if any(p is None for p in parts) else sum(to_decimal(p) for p in parts)
                    provenance = {"metric_type": "total_value", "aggregation": "sum_of_30_day_blocks"}
                else:
                    continue
                if value is not None:
                    result.records.append(
                        record(
                            spec.key,
                            value,
                            spec.unit,
                            window_start,
                            window_end,
                            "window",
                            provenance=provenance,
                        )
                    )
            result.supported.add(spec.key)
        except ProviderError as exc:
            if exc.code not in METRIC_LEVEL_ERRORS:
                raise
            result.unsupported[spec.key] = exc.code

    try:
        media = request_json(
            "GET",
            f"{GRAPH_IG}/{ig_id}/media",
            provider="instagram",
            params={
                "fields": "id,caption,media_type,media_product_type,timestamp,permalink",
                "limit": RECENT_CONTENT_LIMIT,
                "access_token": token,
            },
        ).get("data", [])
    except ProviderError as exc:
        if exc.code not in METRIC_LEVEL_ERRORS:
            raise
        media = []
        result.notes.append("content_unavailable")

    for item in media[:RECENT_CONTENT_LIMIT]:
        media_id = item.get("id")
        published = (item.get("timestamp") or "")[:10]
        try:
            published_day = date.fromisoformat(published)
        except ValueError:
            continue
        label = (item.get("caption") or "").strip().replace("\n", " ")[:140] or f"Publication du {published}"
        values = {}
        for metric in ("views", "reach", "total_interactions"):
            try:
                rows = request_json(
                    "GET",
                    f"{GRAPH_IG}/{media_id}/insights",
                    provider="instagram",
                    params={"metric": metric, "access_token": token},
                ).get("data", [])
            except ProviderError as exc:
                if exc.code not in METRIC_LEVEL_ERRORS:
                    raise
                continue
            for row in rows:
                points = row.get("values") or []
                if points:
                    values[metric] = points[0].get("value")
                elif "total_value" in row:
                    values[metric] = (row.get("total_value") or {}).get("value")
        for metric, value in values.items():
            result.records.append(
                record(
                    f"media_{metric}",
                    value,
                    "count",
                    published_day,
                    published_day,
                    "lifetime",
                    dimension_type="content",
                    dimension_value=media_id,
                    dimension_label=label,
                    provenance={
                        "permalink": item.get("permalink", ""),
                        "mediaType": item.get("media_product_type") or item.get("media_type", ""),
                    },
                )
            )
    return result


# ── Metrics: GA4 ────────────────────────────────────────────────────────────


def _ga_report(property_id, headers, body):
    rows, offset = [], 0
    for _ in range(20):
        payload = request_json(
            "POST",
            f"{GA4_DATA_API}/{property_id}:runReport",
            provider="google",
            headers=headers,
            json_body={**body, "limit": 10000, "offset": offset},
        )
        batch = payload.get("rows", []) or []
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(payload.get("rowCount") or 0):
            break
    return rows


def fetch_ga4(property_id, *, token, start, end, skip_metrics=None) -> FetchResult:
    platform = DigitalPlatform.GOOGLE_ANALYTICS
    headers = {"Authorization": f"Bearer {token}"}
    result = FetchResult()

    prop = request_json("GET", f"{GA4_ADMIN_API}/{property_id}", provider="google", headers=headers)
    tz = prop.get("timeZone", "")
    result.source_timezone = tz

    metadata = request_json(
        "GET", f"{GA4_DATA_API}/{property_id}/metadata", provider="google", headers=headers
    )
    available = {m.get("apiName") for m in metadata.get("metrics", [])}

    wanted = []
    for spec in METRICS[platform]:
        if spec.api_name not in available:
            result.unsupported[spec.key] = "unsupported_metric"
            continue
        wanted.append(spec)

    if any(s.key == "keyEvents" for s in wanted):
        try:
            key_events = request_json(
                "GET",
                f"{GA4_ADMIN_API}/{property_id}/keyEvents",
                provider="google",
                headers=headers,
                params={"pageSize": 50},
            ).get("keyEvents", [])
        except ProviderError as exc:
            if exc.code not in METRIC_LEVEL_ERRORS and exc.code != "resource_inaccessible":
                raise
            key_events = []
        if not key_events:
            wanted = [s for s in wanted if s.key != "keyEvents"]
            result.unsupported["keyEvents"] = "no_key_events"

    if not wanted:
        return result
    metric_names = [{"name": s.api_name} for s in wanted]

    daily = _ga_report(
        property_id,
        headers,
        {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": "date"}],
            "metrics": metric_names,
            "keepEmptyRows": True,
        },
    )
    for row in daily:
        raw_day = (row.get("dimensionValues") or [{}])[0].get("value", "")
        try:
            day = datetime.strptime(raw_day, "%Y%m%d").date()
        except ValueError:
            continue
        for spec, cell in zip(wanted, row.get("metricValues") or []):
            result.records.append(record(spec.key, cell.get("value"), spec.unit, day, day, "day", tz=tz))

    for days, window_start, window_end in _windows(end):
        rows = _ga_report(
            property_id,
            headers,
            {
                "dateRanges": [
                    {"startDate": window_start.isoformat(), "endDate": window_end.isoformat()}
                ],
                "metrics": metric_names,
            },
        )
        if not rows:
            continue
        for spec, cell in zip(wanted, rows[0].get("metricValues") or []):
            result.records.append(
                record(spec.key, cell.get("value"), spec.unit, window_start, window_end, "window", tz=tz)
            )
    result.supported.update(s.key for s in wanted)
    return result


# ── Metrics: Search Console ─────────────────────────────────────────────────

GSC_FIELDS = ("clicks", "impressions", "ctr", "position")


def _gsc_query(site_url, headers, body, *, paginate=False):
    url = f"{SEARCH_CONSOLE_API}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    rows, start_row = [], 0
    row_limit = body.get("rowLimit", 25000)
    for _ in range(20 if paginate else 1):
        payload = request_json(
            "POST", url, provider="google", headers=headers, json_body={**body, "startRow": start_row}
        )
        batch = payload.get("rows", []) or []
        rows.extend(batch)
        if not paginate or len(batch) < row_limit:
            break
        start_row += len(batch)
    return rows


def fetch_search_console(site_url, *, token, start, end, skip_metrics=None) -> FetchResult:
    platform = DigitalPlatform.GOOGLE_SEARCH_CONSOLE
    headers = {"Authorization": f"Bearer {token}"}
    tz = "America/Los_Angeles"
    result = FetchResult(source_timezone=tz)
    base = {"type": "web", "dataState": "final"}
    units = {spec.key: spec.unit for spec in METRICS[platform]}

    daily = _gsc_query(
        site_url,
        headers,
        {**base, "startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["date"], "rowLimit": 25000},
        paginate=True,
    )
    for row in daily:
        try:
            day = date.fromisoformat((row.get("keys") or [""])[0])
        except ValueError:
            continue
        for metric in GSC_FIELDS:
            if metric in row:
                result.records.append(record(metric, row.get(metric), units[metric], day, day, "day", tz=tz))

    for days, window_start, window_end in _windows(end):
        window = {**base, "startDate": window_start.isoformat(), "endDate": window_end.isoformat()}
        totals = _gsc_query(site_url, headers, {**window, "rowLimit": 1})
        if totals:
            for metric in GSC_FIELDS:
                if metric in totals[0]:
                    result.records.append(
                        record(metric, totals[0].get(metric), units[metric], window_start, window_end, "window", tz=tz)
                    )
        for dimension, _label in TOP_DIMENSIONS[platform]:
            rows = _gsc_query(site_url, headers, {**window, "dimensions": [dimension], "rowLimit": TOP_ROWS})
            for rank, row in enumerate(rows, start=1):
                key = (row.get("keys") or [""])[0]
                for metric in GSC_FIELDS:
                    if metric in row:
                        result.records.append(
                            record(
                                metric,
                                row.get(metric),
                                units[metric],
                                window_start,
                                window_end,
                                "window",
                                tz=tz,
                                dimension_type=dimension,
                                dimension_value=key,
                                dimension_label=key,
                                provenance={"rank": rank, "rowLimit": TOP_ROWS},
                            )
                        )
    result.supported.update(GSC_FIELDS)
    return result


FETCHERS = {
    DigitalPlatform.META_FACEBOOK: fetch_facebook,
    DigitalPlatform.META_INSTAGRAM: fetch_instagram,
    DigitalPlatform.GOOGLE_ANALYTICS: fetch_ga4,
    DigitalPlatform.GOOGLE_SEARCH_CONSOLE: fetch_search_console,
}
