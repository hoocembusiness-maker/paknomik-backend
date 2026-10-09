"""
Digital presence API: supplier connections and dashboard, OAuth callback,
and the global ULTRA_ADMIN view. No endpoint ever returns a token.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import rbac
from user.digital_config import (
    MANUAL_SYNC_COOLDOWN_MINUTES,
    OAUTH_BINDING_COOKIE,
    OAUTH_CALLBACK_PREFIX,
    OAUTH_STATE_MAX_AGE_SECONDS,
    PROVIDERS,
    frontend_base_url,
)
from user.digital_http import ProviderError
from user.digital_models import DigitalAuthorization, DigitalConnection
from user.digital_oauth import (
    DigitalFlowError,
    complete_authorization,
    disconnect_platform,
    refresh_available_resources,
    remove_connection,
    select_resources,
    start_authorization,
)
from user.digital_reporting import (
    ComparisonError,
    build_admin_overview,
    build_comparison,
    build_supplier_dashboard,
    connection_summary,
    parse_days,
    platform_cards,
)
from user.digital_sync import sync_connection

FRONTEND_PATH = "/supplier/presence-digitale"


def _supplier_or_denied(request):
    supplier = getattr(request.user, "supplier", None)
    if supplier is None or not request.user.is_active:
        return None, Response(
            {"error": "Un compte fournisseur est requis.", "code": "supplier_required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return supplier, None


def _platform_or_404(platform):
    if platform not in PROVIDERS:
        return Response({"error": "Plateforme inconnue.", "code": "unknown_platform"}, status=404)
    return None


def _flow_error(exc: DigitalFlowError):
    return Response({"error": exc.message, "code": exc.code}, status=exc.status)


class SupplierDigitalPresenceView(APIView):
    """Cards + dashboard for the authenticated supplier only."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        platform = request.query_params.get("platform") or None
        return Response(
            build_supplier_dashboard(
                supplier, days=parse_days(request.query_params.get("days")), platform=platform
            )
        )


class SupplierDigitalConnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, platform):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        missing = _platform_or_404(platform)
        if missing:
            return missing
        try:
            url, binding = start_authorization(request.user, supplier, platform, request=request)
        except DigitalFlowError as exc:
            return _flow_error(exc)
        response = Response({"authorizationUrl": url})
        response.set_cookie(
            OAUTH_BINDING_COOKIE,
            binding,
            max_age=OAUTH_STATE_MAX_AGE_SECONDS,
            httponly=True,
            secure=not settings.DEBUG,
            samesite="Lax",
            path=OAUTH_CALLBACK_PREFIX,
        )
        return response


class SupplierDigitalPlatformView(APIView):
    """DELETE = disconnect the platform and erase its tokens and measures."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, platform):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        missing = _platform_or_404(platform)
        if missing:
            return missing
        try:
            result = disconnect_platform(request.user, supplier, platform, request=request)
        except DigitalFlowError as exc:
            return _flow_error(exc)
        return Response({"success": True, **result})


class SupplierDigitalResourcesView(APIView):
    permission_classes = [IsAuthenticated]

    def _card(self, supplier, platform):
        return next(c for c in platform_cards(supplier) if c["platform"] == platform)

    def get(self, request, platform):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        missing = _platform_or_404(platform)
        if missing:
            return missing
        if request.query_params.get("refresh") == "1":
            authorization = DigitalAuthorization.objects.filter(
                supplier=supplier, platform=platform, status=DigitalAuthorization.Status.ACTIVE
            ).first()
            if authorization is None:
                return Response(
                    {"error": "Connectez d'abord ce compte.", "code": "not_connected"}, status=409
                )
            try:
                refresh_available_resources(authorization)
            except ProviderError as exc:
                return Response(
                    {"error": exc.user_message, "code": exc.code},
                    status=409 if exc.needs_reauth else 502,
                )
        return Response(self._card(supplier, platform))

    def post(self, request, platform):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        missing = _platform_or_404(platform)
        if missing:
            return missing
        ids = request.data.get("externalIds")
        if not isinstance(ids, list):
            return Response(
                {"error": "externalIds doit être une liste.", "code": "invalid_payload"}, status=400
            )
        try:
            connections = select_resources(request.user, supplier, platform, ids, request=request)
        except DigitalFlowError as exc:
            return _flow_error(exc)
        for connection in connections:
            if connection.last_synced_at is None:
                sync_connection(connection)
        return Response(self._card(supplier, platform))


class SupplierDigitalConnectionView(APIView):
    """DELETE one selected resource (and its measures)."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        connection = DigitalConnection.objects.filter(pk=pk, supplier=supplier).first()
        if connection is None:
            return Response({"error": "Connexion introuvable."}, status=404)
        try:
            remove_connection(request.user, connection, request=request)
        except DigitalFlowError as exc:
            return _flow_error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupplierDigitalSyncView(APIView):
    """Manual resync, limited to one per connection per cooldown window."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        connection = (
            DigitalConnection.objects.select_related("authorization")
            .filter(pk=pk, supplier=supplier)
            .first()
        )
        if connection is None:
            return Response({"error": "Connexion introuvable."}, status=404)
        if connection.is_demo:
            return Response(
                {"error": "Les données de démonstration ne se synchronisent pas.", "code": "demo"},
                status=400,
            )
        now = timezone.now()
        cooldown = timedelta(minutes=MANUAL_SYNC_COOLDOWN_MINUTES)
        claimed = (
            DigitalConnection.objects.filter(pk=connection.pk)
            .exclude(last_manual_sync_at__gt=now - cooldown)
            .update(last_manual_sync_at=now)
        )
        if not claimed:
            retry_at = connection.last_manual_sync_at + cooldown
            return Response(
                {
                    "error": f"Une synchronisation manuelle est possible toutes les {MANUAL_SYNC_COOLDOWN_MINUTES} minutes.",
                    "code": "sync_rate_limited",
                    "retryAt": retry_at.isoformat(),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        connection.refresh_from_db()
        outcome = sync_connection(connection)
        rbac.log_audit(
            request.user,
            "digital.sync.manual",
            entity_type="supplier",
            entity_id=supplier.pk,
            new_value={"connectionId": connection.pk, "result": outcome["status"]},
            request=request,
        )
        connection.refresh_from_db()
        return Response(
            {
                "result": {k: str(v) if hasattr(v, "isoformat") else v for k, v in outcome.items()},
                "connection": connection_summary(connection),
            }
        )


class DigitalOAuthCallbackView(APIView):
    """Provider redirect target. Always answers with a redirect to the frontend."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, provider):
        base = f"{frontend_base_url()}{FRONTEND_PATH}"
        try:
            attempt, _authorization = complete_authorization(
                provider,
                request.query_params,
                request.COOKIES.get(OAUTH_BINDING_COOKIE, ""),
                request=request,
            )
            query = {"digital": "connected", "platform": attempt.platform}
        except DigitalFlowError as exc:
            query = {"digitalError": exc.code}
        response = HttpResponseRedirect(f"{base}?{urlencode(query)}")
        response.delete_cookie(OAUTH_BINDING_COOKIE, path=OAUTH_CALLBACK_PREFIX)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


# ── Global admin view (ULTRA_ADMIN) ─────────────────────────────────────────


def _deny_unless_ultra(request):
    user = request.user
    if not (user.is_authenticated and user.is_active and user.is_staff and rbac.is_ultra_admin(user)):
        return Response(
            {
                "error": "La vue globale Présence digitale est réservée à l'ultra-administrateur.",
                "code": "ultra_admin_required",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _int_or_none(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class AdminDigitalOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        params = request.query_params
        payload = build_admin_overview(
            category=_int_or_none(params.get("category")),
            wilaya=params.get("wilaya") or None,
            platform=params.get("platform") or None,
            status=params.get("status") or None,
            include_demo=params.get("includeDemo") == "1",
        )
        rbac.log_audit(request.user, "digital.admin.overview", entity_type="digital_presence", request=request)
        return Response(payload)


class AdminDigitalSupplierView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, supplier_id):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        from user.models import Supplier

        supplier = Supplier.objects.filter(pk=supplier_id).first()
        if supplier is None:
            return Response({"error": "Fournisseur introuvable."}, status=404)
        rbac.log_audit(
            request.user,
            "digital.admin.supplier_detail",
            entity_type="supplier",
            entity_id=supplier.pk,
            request=request,
        )
        return Response(
            build_supplier_dashboard(
                supplier,
                days=parse_days(request.query_params.get("days")),
                platform=request.query_params.get("platform") or None,
                include_config_details=True,
            )
        )


class AdminDigitalCompareView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        params = request.query_params
        supplier_ids = [
            value
            for value in (_int_or_none(v) for v in (params.get("suppliers") or "").split(","))
            if value
        ]
        try:
            payload = build_comparison(
                platform=params.get("platform"),
                metric=params.get("metric"),
                days=parse_days(params.get("days")),
                supplier_ids=supplier_ids or None,
                include_demo=params.get("includeDemo") == "1",
            )
        except ComparisonError as exc:
            return Response({"error": str(exc), "code": "not_comparable"}, status=400)
        return Response(payload)
