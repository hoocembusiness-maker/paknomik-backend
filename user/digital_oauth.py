"""
OAuth flow and resource management for digital presence integrations.

`state` = signed nonce (django.core.signing, 10 min). The nonce points to a
single-use `DigitalOAuthAttempt` row holding the supplier, the user, the PKCE
verifier (encrypted) and the hash of an HttpOnly browser-binding cookie set
when the flow started — so a callback opened in another browser is refused.
The supplier always comes from that server-side row, never from the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone

from user import rbac
from user.digital_config import (
    OAUTH_STATE_MAX_AGE_SECONDS,
    PROVIDERS,
    configuration_problem,
)
from user.digital_crypto import decrypt_token, encrypt_token
from user.digital_http import ProviderError
from user.digital_models import (
    DigitalAuthorization,
    DigitalConnection,
    DigitalOAuthAttempt,
    DigitalPlatform,
)
from user.digital_providers import (
    build_authorization_url,
    exchange_code,
    list_resources,
    missing_scopes,
    revoke_grant,
)

STATE_SALT = "paknomik.digital.oauth.state"

CALLBACK_ERRORS = {
    "access_denied": "Autorisation annulée : aucun compte n'a été connecté.",
    "invalid_state": "Lien d'autorisation invalide ou expiré : relancez la connexion depuis Paknomik.",
    "session_mismatch": "La connexion doit être terminée dans le navigateur où elle a été lancée.",
    "supplier_mismatch": "Votre compte n'est plus autorisé à gérer ce fournisseur.",
    "insufficient_scope": "Accès en lecture refusé : acceptez toutes les autorisations demandées pour connecter ce compte.",
    "exchange_failed": "La source n'a pas validé l'autorisation. Réessayez dans quelques instants.",
    "not_configured": "Cette intégration n'est pas encore configurée sur le serveur Paknomik.",
    "no_resource": "Aucune ressource accessible n'a été trouvée avec ce compte.",
}


class DigitalFlowError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 400):
        self.code = code
        self.message = message or CALLBACK_ERRORS.get(code, code)
        self.status = status
        super().__init__(self.message)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def assert_can_manage_supplier(user, supplier) -> None:
    """Only an active user attached to the supplier may manage its integrations."""
    if not user or not user.is_authenticated or not user.is_active:
        raise DigitalFlowError("supplier_mismatch", status=403)
    if supplier is None or user.supplier_id != supplier.pk:
        raise DigitalFlowError("supplier_mismatch", status=403)


def start_authorization(user, supplier, platform: str, *, request=None) -> tuple[str, str]:
    """Return (authorization_url, browser_binding_token)."""
    assert_can_manage_supplier(user, supplier)
    if platform not in PROVIDERS:
        raise DigitalFlowError("invalid_state", "Plateforme inconnue.", status=404)
    problem = configuration_problem(platform)
    if problem:
        raise DigitalFlowError("not_configured", CALLBACK_ERRORS["not_configured"], status=503)

    config = PROVIDERS[platform]
    DigitalOAuthAttempt.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()

    nonce = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair() if config.pkce else ("", None)
    DigitalOAuthAttempt.objects.create(
        nonce=nonce,
        supplier=supplier,
        user=user,
        platform=platform,
        code_verifier_enc=encrypt_token(verifier),
        browser_binding_hash=_hash(binding),
    )
    state = signing.dumps({"n": nonce, "p": platform}, salt=STATE_SALT, compress=True)
    rbac.log_audit(
        user,
        "digital.oauth.start",
        entity_type="supplier",
        entity_id=supplier.pk,
        new_value={"platform": platform},
        request=request,
    )
    return build_authorization_url(config, state=state, code_challenge=challenge), binding


def _consume_attempt(callback_slug: str, state: str, binding_cookie: str) -> DigitalOAuthAttempt:
    try:
        data = signing.loads(state or "", salt=STATE_SALT, max_age=OAUTH_STATE_MAX_AGE_SECONDS)
    except signing.BadSignature:
        raise DigitalFlowError("invalid_state") from None
    platform = data.get("p")
    if platform not in PROVIDERS or PROVIDERS[platform].callback_slug != callback_slug:
        raise DigitalFlowError("invalid_state")

    attempt = (
        DigitalOAuthAttempt.objects.select_related("supplier", "user")
        .filter(nonce=data.get("n"), platform=platform)
        .first()
    )
    if attempt is None or attempt.consumed_at is not None:
        raise DigitalFlowError("invalid_state")
    if not binding_cookie or not hmac.compare_digest(_hash(binding_cookie), attempt.browser_binding_hash):
        raise DigitalFlowError("session_mismatch")
    claimed = DigitalOAuthAttempt.objects.filter(pk=attempt.pk, consumed_at__isnull=True).update(
        consumed_at=timezone.now()
    )
    if claimed != 1:
        raise DigitalFlowError("invalid_state")
    return attempt


def complete_authorization(callback_slug: str, params, binding_cookie: str, *, request=None):
    """Handle the provider redirect. Returns (attempt, authorization)."""
    attempt = _consume_attempt(callback_slug, params.get("state", ""), binding_cookie)
    if params.get("error"):
        raise DigitalFlowError("access_denied")
    code = params.get("code", "")
    if not code:
        raise DigitalFlowError("invalid_state")

    user, supplier, platform = attempt.user, attempt.supplier, attempt.platform
    if not user.is_active or user.supplier_id != supplier.pk:
        raise DigitalFlowError("supplier_mismatch", status=403)
    if configuration_problem(platform):
        raise DigitalFlowError("not_configured", status=503)

    config = PROVIDERS[platform]
    try:
        bundle = exchange_code(config, code=code, code_verifier=decrypt_token(attempt.code_verifier_enc))
    except ProviderError:
        raise DigitalFlowError("exchange_failed", status=502) from None
    if not bundle.access_token:
        raise DigitalFlowError("exchange_failed", status=502)
    if missing_scopes(config, bundle.scopes):
        revoke_grant(config, access_token=bundle.access_token, refresh_token=bundle.refresh_token)
        raise DigitalFlowError("insufficient_scope")

    try:
        resources, resource_tokens = list_resources(platform, access_token=bundle.access_token)
    except ProviderError as exc:
        if exc.needs_reauth:
            raise DigitalFlowError("insufficient_scope") from None
        raise DigitalFlowError("exchange_failed", status=502) from None

    now = timezone.now()
    with transaction.atomic():
        authorization, _created = DigitalAuthorization.objects.select_for_update().get_or_create(
            supplier=supplier, platform=platform
        )
        authorization.granted_by = user
        authorization.external_account_id = bundle.account_id or (resources[0]["id"] if len(resources) == 1 else "")
        authorization.external_account_name = bundle.account_name or (
            resources[0]["name"] if platform == DigitalPlatform.META_INSTAGRAM and resources else ""
        )
        authorization.scopes = bundle.scopes or list(config.scopes)
        authorization.access_token_enc = encrypt_token(bundle.access_token)
        if bundle.refresh_token:
            authorization.refresh_token_enc = encrypt_token(bundle.refresh_token)
        authorization.token_expires_at = bundle.expires_at
        authorization.status = DigitalAuthorization.Status.ACTIVE
        authorization.available_resources = resources
        authorization.resources_listed_at = now
        authorization.last_error_code = ""
        authorization.last_error_message = ""
        authorization.last_authorized_at = now
        authorization.save()

        available = {r["id"]: r for r in resources}
        for connection in DigitalConnection.objects.filter(supplier=supplier, platform=platform):
            resource = available.get(connection.external_id)
            connection.authorization = authorization
            if resource is None:
                connection.status = DigitalConnection.Status.ERROR
                connection.last_error_code = "resource_inaccessible"
                connection.last_error_message = (
                    "Cette ressource n'est plus accessible avec le compte autorisé : choisissez-en une autre."
                )
            elif not resource.get("eligible"):
                connection.status = DigitalConnection.Status.INELIGIBLE
                connection.last_error_code = "ineligible_account"
                connection.last_error_message = resource.get("reason", "")
            else:
                connection.status = DigitalConnection.Status.ACTIVE
                connection.last_error_code = ""
                connection.last_error_message = ""
                connection.last_connected_at = now
                if connection.external_id in resource_tokens:
                    connection.resource_token_enc = encrypt_token(resource_tokens[connection.external_id])
            connection.save()

        eligible = [r for r in resources if r.get("eligible")]
        has_connections = DigitalConnection.objects.filter(supplier=supplier, platform=platform).exists()
        if len(eligible) == 1 and not has_connections:
            _create_connection(supplier, authorization, eligible[0], resource_tokens, now)

    rbac.log_audit(
        user,
        "digital.oauth.connected",
        entity_type="supplier",
        entity_id=supplier.pk,
        new_value={
            "platform": platform,
            "resources": len(resources),
            "eligible": len(eligible),
            "scopes": authorization.scopes,
        },
        request=request,
    )
    return attempt, authorization


def _create_connection(supplier, authorization, resource, resource_tokens, now):
    connection, _ = DigitalConnection.objects.update_or_create(
        supplier=supplier,
        platform=authorization.platform,
        external_id=resource["id"],
        defaults={
            "authorization": authorization,
            "resource_type": resource.get("resourceType", ""),
            "display_name": resource.get("name", "")[:255],
            "status": DigitalConnection.Status.ACTIVE,
            "last_connected_at": now,
            "last_error_code": "",
            "last_error_message": "",
            "is_demo": False,
        },
    )
    token = resource_tokens.get(resource["id"])
    if token:
        connection.resource_token_enc = encrypt_token(token)
        connection.save(update_fields=["resource_token_enc"])
    return connection


def refresh_available_resources(authorization: DigitalAuthorization):
    """Re-list resources reachable by the grant. Returns (resources, tokens)."""
    from user.digital_sync import access_token_for

    if authorization.platform == DigitalPlatform.META_FACEBOOK:
        token = decrypt_token(authorization.access_token_enc)
        if not token:
            raise ProviderError("token_expired")
    else:
        token = access_token_for(authorization)
    resources, tokens = list_resources(authorization.platform, access_token=token)
    authorization.available_resources = resources
    authorization.resources_listed_at = timezone.now()
    authorization.save(update_fields=["available_resources", "resources_listed_at", "updated_at"])
    return resources, tokens


def select_resources(user, supplier, platform: str, external_ids: list[str], *, request=None):
    """Replace the selected resources of a platform with `external_ids`."""
    assert_can_manage_supplier(user, supplier)
    authorization = DigitalAuthorization.objects.filter(supplier=supplier, platform=platform).first()
    if authorization is None or authorization.status != DigitalAuthorization.Status.ACTIVE:
        raise DigitalFlowError("token_expired", "Connectez d'abord ce compte.", status=409)

    wanted = list(dict.fromkeys(str(i) for i in external_ids if str(i).strip()))
    if not wanted:
        raise DigitalFlowError("no_resource", "Sélectionnez au moins une ressource.", status=400)

    resource_tokens = {}
    resources = authorization.available_resources or []
    if platform == DigitalPlatform.META_FACEBOOK:
        try:
            resources, resource_tokens = refresh_available_resources(authorization)
        except ProviderError as exc:
            raise DigitalFlowError(exc.code, exc.user_message, status=409 if exc.needs_reauth else 502) from None

    available = {r["id"]: r for r in resources}
    for external_id in wanted:
        resource = available.get(external_id)
        if resource is None:
            raise DigitalFlowError(
                "resource_inaccessible",
                "Cette ressource n'est pas accessible avec le compte autorisé.",
                status=400,
            )
        if not resource.get("eligible"):
            raise DigitalFlowError("ineligible_account", resource.get("reason", ""), status=400)

    now = timezone.now()
    with transaction.atomic():
        removed = DigitalConnection.objects.filter(supplier=supplier, platform=platform).exclude(
            external_id__in=wanted
        )
        removed_count = removed.count()
        removed.delete()
        connections = [
            _create_connection(supplier, authorization, available[external_id], resource_tokens, now)
            for external_id in wanted
        ]
    rbac.log_audit(
        user,
        "digital.resources.selected",
        entity_type="supplier",
        entity_id=supplier.pk,
        new_value={"platform": platform, "selected": len(connections), "removed": removed_count},
        request=request,
    )
    return connections


def remove_connection(user, connection: DigitalConnection, *, request=None):
    assert_can_manage_supplier(user, connection.supplier)
    payload = {"platform": connection.platform, "connectionId": connection.pk}
    connection.delete()
    rbac.log_audit(
        user,
        "digital.resource.removed",
        entity_type="supplier",
        entity_id=user.supplier_id,
        new_value=payload,
        request=request,
    )


def disconnect_platform(user, supplier, platform: str, *, request=None) -> dict:
    """Revoke the grant (best effort) and delete every stored token and measure."""
    assert_can_manage_supplier(user, supplier)
    authorization = DigitalAuthorization.objects.filter(supplier=supplier, platform=platform).first()
    revoked = False
    if authorization is not None:
        try:
            access = decrypt_token(authorization.access_token_enc)
            refresh = decrypt_token(authorization.refresh_token_enc)
        except Exception:  # noqa: BLE001 — deletion must proceed even if keys rotated
            access = refresh = ""
        revoked = revoke_grant(PROVIDERS[platform], access_token=access, refresh_token=refresh)
    with transaction.atomic():
        connections = DigitalConnection.objects.filter(supplier=supplier, platform=platform)
        removed = connections.count()
        connections.delete()
        if authorization is not None:
            authorization.delete()
    rbac.log_audit(
        user,
        "digital.platform.disconnected",
        entity_type="supplier",
        entity_id=supplier.pk,
        new_value={"platform": platform, "connectionsRemoved": removed, "revokedAtProvider": revoked},
        request=request,
    )
    return {"connectionsRemoved": removed, "revokedAtProvider": revoked}
