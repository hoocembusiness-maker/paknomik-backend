"""
Digital presence (Meta + Google) — OAuth grants, selected resources and metrics.

Tokens are stored encrypted (see `user.digital_crypto`) and never leave the
server. A supplier only ever sees rows linked to its own `supplier` FK; the
supplier is always taken from the authenticated session, never from the browser.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class DigitalPlatform(models.TextChoices):
    META_FACEBOOK = "meta_facebook", "Page Facebook"
    META_INSTAGRAM = "meta_instagram", "Instagram professionnel"
    GOOGLE_ANALYTICS = "google_analytics", "Google Analytics 4"
    GOOGLE_SEARCH_CONSOLE = "google_search_console", "Google Search Console"


class DigitalAuthorization(models.Model):
    """One OAuth grant of a supplier for one platform card."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        NEEDS_REAUTH = "needs_reauth", "Reconnexion requise"
        REVOKED = "revoked", "Révoquée"

    supplier = models.ForeignKey(
        "user.Supplier", on_delete=models.CASCADE, related_name="digital_authorizations"
    )
    platform = models.CharField(max_length=32, choices=DigitalPlatform.choices)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="digital_authorizations_granted",
    )
    external_account_id = models.CharField(max_length=191, blank=True)
    external_account_name = models.CharField(max_length=255, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    access_token_enc = models.TextField(blank=True)
    refresh_token_enc = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    #: Resources the grant can reach: [{id, name, eligible, reason, ...}] — no tokens.
    available_resources = models.JSONField(default=list, blank=True)
    resources_listed_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_authorized_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "platform"], name="digital_auth_unique_supplier_platform"
            )
        ]

    def __str__(self):
        return f"{self.supplier_id} · {self.platform} ({self.status})"


class DigitalConnection(models.Model):
    """A resource selected by the supplier: Page, IG account, GA4 property or GSC site."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Connectée"
        NEEDS_REAUTH = "needs_reauth", "Reconnexion requise"
        ERROR = "error", "Erreur"
        INELIGIBLE = "ineligible", "Non admissible"

    supplier = models.ForeignKey(
        "user.Supplier", on_delete=models.CASCADE, related_name="digital_connections"
    )
    authorization = models.ForeignKey(
        DigitalAuthorization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="connections",
    )
    platform = models.CharField(max_length=32, choices=DigitalPlatform.choices, db_index=True)
    resource_type = models.CharField(max_length=32)
    external_id = models.CharField(max_length=500)
    display_name = models.CharField(max_length=255, blank=True)
    #: Resource-scoped token (Facebook Page token). Encrypted, never serialized.
    resource_token_enc = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    source_timezone = models.CharField(max_length=64, blank=True)
    #: Capability cache: {"unsupportedMetrics": {key: {code, checkedAt}}, ...}
    metadata = models.JSONField(default=dict, blank=True)
    #: Seeded by `seed_digital_presence_demo` (DEBUG only) — never real measures.
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_sync_attempt_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_manual_sync_at = models.DateTimeField(null=True, blank=True)
    data_start = models.DateField(null=True, blank=True)
    data_end = models.DateField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["platform", "display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "platform", "external_id"],
                name="digital_connection_unique_resource",
            )
        ]

    def __str__(self):
        return f"{self.supplier_id} · {self.platform} · {self.display_name or self.external_id}"


class DigitalMetric(models.Model):
    """One stored measure. Absent data is absent (no row) or null — never zero."""

    class Granularity(models.TextChoices):
        DAY = "day", "Jour"
        WINDOW = "window", "Fenêtre"
        LIFETIME = "lifetime", "Cumul du contenu"

    supplier = models.ForeignKey(
        "user.Supplier", on_delete=models.CASCADE, related_name="digital_metrics"
    )
    connection = models.ForeignKey(
        DigitalConnection, on_delete=models.CASCADE, related_name="metrics"
    )
    platform = models.CharField(max_length=32, choices=DigitalPlatform.choices, db_index=True)
    metric_key = models.CharField(max_length=64)
    value = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    unit = models.CharField(max_length=20, default="count")
    granularity = models.CharField(max_length=10, choices=Granularity.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    source_timezone = models.CharField(max_length=64, blank=True)
    dimension_type = models.CharField(max_length=20, blank=True)
    #: sha1 of `dimension_value` so long URLs fit the unique index.
    dimension_key = models.CharField(max_length=40, blank=True)
    dimension_value = models.TextField(blank=True)
    dimension_label = models.CharField(max_length=500, blank=True)
    fetched_at = models.DateTimeField()
    provenance = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["connection", "metric_key", "granularity", "period_end"]),
            models.Index(fields=["platform", "metric_key", "granularity", "period_end"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "connection",
                    "metric_key",
                    "granularity",
                    "period_start",
                    "period_end",
                    "dimension_type",
                    "dimension_key",
                ],
                name="digital_metric_unique_measure",
            )
        ]

    def __str__(self):
        return f"{self.platform}:{self.metric_key} {self.period_start}→{self.period_end}"


class DigitalOAuthAttempt(models.Model):
    """Single-use server-side half of the OAuth `state` (CSRF + PKCE + supplier binding)."""

    nonce = models.CharField(max_length=64, unique=True)
    supplier = models.ForeignKey(
        "user.Supplier", on_delete=models.CASCADE, related_name="digital_oauth_attempts"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="digital_oauth_attempts"
    )
    platform = models.CharField(max_length=32, choices=DigitalPlatform.choices)
    code_verifier_enc = models.TextField(blank=True)
    browser_binding_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.platform} attempt for supplier {self.supplier_id}"
