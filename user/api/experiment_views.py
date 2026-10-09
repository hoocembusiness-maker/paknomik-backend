"""
API for A/B tests and feature flags (experiments).
"""

from __future__ import annotations

from django.core.cache import cache
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user.events import log_buyer_event
from user.models import Event, ExperimentConfig


def _require_staff(user):
    return user.is_authenticated and user.is_staff


def _staff_forbidden():
    return Response(
        {"error": "Staff access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


def _config_cache_key(key: str) -> str:
    return f"experiment_config:{key}"


def _serialize_public(config: ExperimentConfig) -> dict:
    return {
        "key": config.key,
        "enabled": config.enabled,
        "splitPercentV2": config.split_percent_v2,
        "forcedVariant": config.forced_variant or None,
        "updatedAt": config.updated_at.isoformat(),
    }


def _serialize_admin(config: ExperimentConfig) -> dict:
    data = _serialize_public(config)
    data.update(
        {
            "updatedBy": config.updated_by.email if config.updated_by_id else None,
            "impressions": {
                "control": config.impressions_control,
                "v2": config.impressions_v2,
            },
        }
    )
    return data


def _get_config(key: str) -> ExperimentConfig:
    return get_object_or_404(ExperimentConfig, key=key)


def _invalidate_config_cache(key: str) -> None:
    cache.delete(_config_cache_key(key))


class ExperimentConfigPublicView(APIView):
    """Lightweight public config for client-side variant selection."""

    permission_classes = [AllowAny]

    def get(self, request, key: str):
        cache_key = _config_cache_key(key)
        cached = cache.get(cache_key)
        if cached is not None:
            response = Response(cached)
            response["Cache-Control"] = "public, max-age=10"
            return response

        config = _get_config(key)
        payload = _serialize_public(config)
        cache.set(cache_key, payload, timeout=10)

        response = Response(payload)
        response["Cache-Control"] = "public, max-age=10"
        return response


class ExperimentImpressionView(APIView):
    """Increment impression counters for an experiment variant."""

    permission_classes = [AllowAny]

    def post(self, request, key: str):
        variant = (request.data.get("variant") or "").strip().lower()
        if variant not in {"control", "v2"}:
            return Response(
                {"error": "variant must be 'control' or 'v2'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        config = _get_config(key)
        field = "impressions_v2" if variant == "v2" else "impressions_control"
        ExperimentConfig.objects.filter(pk=config.pk).update(
            **{field: F(field) + 1},
            updated_at=timezone.now(),
        )
        _invalidate_config_cache(key)

        log_buyer_event(
            request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            Event.EventType.BUYER_PAGE_VIEW,
            {
                "event_name": "experiment_viewed",
                "experiment_id": key,
                "variant": variant,
            },
            request=request,
        )

        return Response({"ok": True})


class AdminExperimentConfigView(APIView):
    """Read or update experiment configuration (staff only)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, key: str):
        if not _require_staff(request.user):
            return _staff_forbidden()
        config = _get_config(key)
        return Response(_serialize_admin(config))

    def put(self, request, key: str):
        if not _require_staff(request.user):
            return _staff_forbidden()

        config = _get_config(key)
        data = request.data or {}

        if "enabled" in data:
            config.enabled = bool(data["enabled"])

        if "splitPercentV2" in data:
            try:
                split = int(data["splitPercentV2"])
            except (TypeError, ValueError):
                return Response(
                    {"error": "splitPercentV2 must be an integer between 0 and 100"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if split < 0 or split > 100:
                return Response(
                    {"error": "splitPercentV2 must be between 0 and 100"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            config.split_percent_v2 = split

        if "forcedVariant" in data:
            forced = data["forcedVariant"]
            if forced is None or forced == "":
                config.forced_variant = None
            elif forced in {"control", "v2"}:
                config.forced_variant = forced
            else:
                return Response(
                    {"error": "forcedVariant must be null, 'control', or 'v2'"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        config.updated_by = request.user
        config.save()
        _invalidate_config_cache(key)

        return Response(_serialize_admin(config))
