"""
Synchronization of digital presence metrics.

Runs from the daily `sync_digital_presence` command and from the rate-limited
manual action. Writes are idempotent upserts on the measure's natural key, so
re-running a sync never duplicates rows. Missing values are never written as 0.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from user.digital_config import (
    BACKFILL_DAYS,
    DATA_LAG_DAYS,
    INCREMENTAL_DAYS,
    PROVIDERS,
    SCHEDULED_SYNC_MIN_HOURS,
    UNSUPPORTED_METRIC_RECHECK_DAYS,
)
from user.digital_crypto import TokenEncryptionUnavailable, decrypt_token, encrypt_token
from user.digital_http import ERROR_MESSAGES, ProviderError
from user.digital_models import DigitalAuthorization, DigitalConnection, DigitalMetric, DigitalPlatform
from user.digital_providers import FETCHERS, refresh_access_token

logger = logging.getLogger(__name__)

UNIQUE_FIELDS = [
    "connection",
    "metric_key",
    "granularity",
    "period_start",
    "period_end",
    "dimension_type",
    "dimension_key",
]
UPDATE_FIELDS = [
    "value",
    "unit",
    "source_timezone",
    "dimension_value",
    "dimension_label",
    "fetched_at",
    "provenance",
]

#: Refresh margin before expiry, per provider.
REFRESH_MARGIN = {
    "google": timedelta(minutes=2),
    "instagram": timedelta(days=7),
    "meta": timedelta(0),
}


def dimension_key(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest() if value else ""


def upsert_records(connection: DigitalConnection, records: list[dict], *, fetched_at=None) -> int:
    """Insert or update measures. Returns the number of distinct measures written."""
    fetched_at = fetched_at or timezone.now()
    unique: dict[tuple, DigitalMetric] = {}
    for rec in records:
        dim_key = dimension_key(rec.get("dimension_value", ""))
        key = (
            rec["metric_key"],
            rec["granularity"],
            rec["period_start"],
            rec["period_end"],
            rec.get("dimension_type", ""),
            dim_key,
        )
        unique[key] = DigitalMetric(
            supplier_id=connection.supplier_id,
            connection=connection,
            platform=connection.platform,
            metric_key=rec["metric_key"],
            value=rec["value"],
            unit=rec.get("unit", "count"),
            granularity=rec["granularity"],
            period_start=rec["period_start"],
            period_end=rec["period_end"],
            source_timezone=rec.get("source_timezone", ""),
            dimension_type=rec.get("dimension_type", ""),
            dimension_key=dim_key,
            dimension_value=rec.get("dimension_value", ""),
            dimension_label=rec.get("dimension_label", ""),
            fetched_at=fetched_at,
            provenance=rec.get("provenance") or {},
        )
    rows = list(unique.values())
    for offset in range(0, len(rows), 500):
        DigitalMetric.objects.bulk_create(
            rows[offset : offset + 500],
            update_conflicts=True,
            unique_fields=UNIQUE_FIELDS,
            update_fields=UPDATE_FIELDS,
        )
    return len(rows)


def _save_bundle(authorization: DigitalAuthorization, bundle):
    authorization.access_token_enc = encrypt_token(bundle.access_token)
    if bundle.refresh_token:
        authorization.refresh_token_enc = encrypt_token(bundle.refresh_token)
    if bundle.expires_at:
        authorization.token_expires_at = bundle.expires_at
    authorization.save(
        update_fields=["access_token_enc", "refresh_token_enc", "token_expires_at", "updated_at"]
    )


def access_token_for(authorization: DigitalAuthorization, *, now=None) -> str:
    """Decrypted access token, refreshed first when the provider allows it."""
    now = now or timezone.now()
    config = PROVIDERS[authorization.platform]
    try:
        access = decrypt_token(authorization.access_token_enc)
        refresh = decrypt_token(authorization.refresh_token_enc)
    except TokenEncryptionUnavailable:
        raise ProviderError("decrypt_failed") from None
    if not access and not refresh:
        raise ProviderError("token_expired")

    expires = authorization.token_expires_at
    margin = REFRESH_MARGIN.get(config.provider, timedelta(0))
    expiring = expires is not None and expires - margin <= now
    if expiring or not access:
        bundle = refresh_access_token(config, access_token=access, refresh_token=refresh)
        if bundle is None:
            if expires is not None and expires <= now:
                raise ProviderError("token_expired")
        else:
            _save_bundle(authorization, bundle)
            access = bundle.access_token
    return access


def _skip_metrics(connection: DigitalConnection, now) -> set[str]:
    skipped = set()
    for key, info in (connection.metadata.get("unsupportedMetrics") or {}).items():
        checked = info.get("checkedAt") if isinstance(info, dict) else None
        try:
            checked_at = datetime.fromisoformat(checked) if checked else None
        except ValueError:
            checked_at = None
        if checked_at and now - checked_at < timedelta(days=UNSUPPORTED_METRIC_RECHECK_DAYS):
            skipped.add(key)
    return skipped


def mark_needs_reauth(connection: DigitalConnection, error: ProviderError):
    connection.status = DigitalConnection.Status.NEEDS_REAUTH
    connection.last_error_code = error.code
    connection.last_error_message = error.user_message
    connection.save(update_fields=["status", "last_error_code", "last_error_message"])
    if connection.authorization_id:
        DigitalAuthorization.objects.filter(pk=connection.authorization_id).update(
            status=DigitalAuthorization.Status.NEEDS_REAUTH,
            last_error_code=error.code,
            last_error_message=error.user_message,
        )
        DigitalConnection.objects.filter(
            authorization_id=connection.authorization_id
        ).exclude(pk=connection.pk).update(
            status=DigitalConnection.Status.NEEDS_REAUTH,
            last_error_code=error.code,
            last_error_message=error.user_message,
        )


def sync_window(connection: DigitalConnection, today: date) -> tuple[date, date]:
    end = today - timedelta(days=DATA_LAG_DAYS[connection.platform])
    has_history = connection.data_start is not None and connection.metrics.filter(
        granularity=DigitalMetric.Granularity.DAY
    ).exists()
    span = INCREMENTAL_DAYS if has_history else BACKFILL_DAYS
    return end - timedelta(days=span - 1), end


def sync_connection(connection: DigitalConnection, *, today: date | None = None) -> dict:
    """Fetch and store metrics for one resource. Never raises provider errors."""
    now = timezone.now()
    today = today or timezone.localdate()
    if connection.is_demo:
        return {"status": "skipped", "reason": "demo"}

    connection.last_sync_attempt_at = now
    connection.save(update_fields=["last_sync_attempt_at"])

    authorization = connection.authorization
    try:
        if authorization is None or authorization.status != DigitalAuthorization.Status.ACTIVE:
            raise ProviderError("token_expired")
        if connection.platform == DigitalPlatform.META_FACEBOOK:
            try:
                token = decrypt_token(connection.resource_token_enc)
            except TokenEncryptionUnavailable:
                raise ProviderError("decrypt_failed") from None
            if not token:
                raise ProviderError("token_expired")
        else:
            token = access_token_for(authorization, now=now)

        start, end = sync_window(connection, today)
        fetcher = FETCHERS[connection.platform]
        result = fetcher(
            connection.external_id,
            token=token,
            start=start,
            end=end,
            skip_metrics=_skip_metrics(connection, now),
        )
    except ProviderError as error:
        if error.needs_reauth:
            mark_needs_reauth(connection, error)
        else:
            connection.status = DigitalConnection.Status.ERROR
            connection.last_error_code = error.code
            connection.last_error_message = error.user_message
            connection.save(update_fields=["status", "last_error_code", "last_error_message"])
        logger.warning(
            "digital sync failed connection=%s platform=%s code=%s",
            connection.pk,
            connection.platform,
            error.code,
        )
        return {"status": "error", "code": error.code, "message": error.user_message}

    with transaction.atomic():
        written = upsert_records(connection, result.records, fetched_at=now)
        unsupported = dict(connection.metadata.get("unsupportedMetrics") or {})
        for key in result.supported:
            unsupported.pop(key, None)
        for key, code in result.unsupported.items():
            unsupported[key] = {"code": code, "checkedAt": now.isoformat()}
        metadata = dict(connection.metadata)
        metadata["unsupportedMetrics"] = unsupported
        metadata["notes"] = sorted(set(result.notes))
        connection.metadata = metadata
        if result.source_timezone:
            connection.source_timezone = result.source_timezone
        connection.data_start = min(filter(None, [connection.data_start, start]))
        connection.data_end = end
        connection.last_synced_at = now
        connection.status = DigitalConnection.Status.ACTIVE
        if "insights_unavailable" in result.notes:
            connection.last_error_code = "insights_unavailable"
            connection.last_error_message = ERROR_MESSAGES["insights_unavailable"]
        else:
            connection.last_error_code = ""
            connection.last_error_message = ""
        connection.save()

    logger.info(
        "digital sync ok connection=%s platform=%s measures=%s", connection.pk, connection.platform, written
    )
    return {"status": "ok", "measures": written, "periodStart": start, "periodEnd": end}


def due_connections(*, force=False, now=None):
    now = now or timezone.now()
    qs = DigitalConnection.objects.filter(
        is_demo=False,
        status__in=[DigitalConnection.Status.ACTIVE, DigitalConnection.Status.ERROR],
        authorization__status=DigitalAuthorization.Status.ACTIVE,
    ).select_related("authorization")
    if not force:
        cutoff = now - timedelta(hours=SCHEDULED_SYNC_MIN_HOURS)
        qs = qs.filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lt=cutoff))
    return qs


def sync_all(*, force=False, today=None) -> dict:
    summary = {"ok": 0, "error": 0, "skipped": 0}
    for connection in due_connections(force=force):
        outcome = sync_connection(connection, today=today)
        summary[outcome["status"]] = summary.get(outcome["status"], 0) + 1
    return summary
