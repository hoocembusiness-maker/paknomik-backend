"""Market Dashboards API (Market Intelligence): aggregated, anonymized marketplace activity."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import market_dashboard_commercial, market_dashboard_visibility, market_dashboards
from user.entitlements import FEATURE_MARKET_ANALYSIS, require_feature
from user.reporting_period import resolve_report_period

PERIOD_ALIASES = {"today": "1d", "last7": "7d", "last30": "30d", "current_month": "mtd"}


def _access_error(request):
    user = request.user
    if user.is_staff or user.is_superuser:
        return None
    if not user.supplier_id:
        return Response(
            {"error": "Les Market Dashboards sont réservés aux comptes fournisseurs."},
            status=status.HTTP_403_FORBIDDEN,
        )
    require_feature(user.supplier, FEATURE_MARKET_ANALYSIS)
    return None


def _period(request):
    params = request.query_params.copy()
    preset = (params.get("period") or "").strip().lower()
    if preset in PERIOD_ALIASES:
        params["period"] = PERIOD_ALIASES[preset]
    if not params.get("period") and not (params.get("start") and params.get("end")):
        params["period"] = "30d"
    return resolve_report_period(params)


def _granularity(request, allowed):
    value = (request.query_params.get("granularity") or "day").strip().lower()
    return value if value in allowed else None


class _MarketDashboardView(APIView):
    permission_classes = [IsAuthenticated]
    allowed_granularities: tuple[str, ...] = ()

    def build(self, period, granularity):
        raise NotImplementedError

    def get(self, request):
        denied = _access_error(request)
        if denied is not None:
            return denied
        granularity = _granularity(request, self.allowed_granularities)
        if granularity is None:
            return Response(
                {"error": "Granularité non disponible pour cette analyse."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        period = _period(request)
        if period.days > 366:
            return Response(
                {"error": "La période ne peut pas dépasser 12 mois."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.build(period, granularity))


class MarketDashboardSessionsView(_MarketDashboardView):
    allowed_granularities = market_dashboards.SESSION_GRANULARITIES

    def build(self, period, granularity):
        return market_dashboards.build_sessions_dashboard(period, granularity)


class MarketDashboardSearchView(_MarketDashboardView):
    allowed_granularities = market_dashboards.SEARCH_GRANULARITIES

    def build(self, period, granularity):
        return market_dashboards.build_search_dashboard(period, granularity)


class MarketDashboardVisibilityView(_MarketDashboardView):
    allowed_granularities = market_dashboard_visibility.VISIBILITY_GRANULARITIES

    def build(self, period, granularity):
        return market_dashboard_visibility.build_visibility_dashboard(period, granularity)


class MarketDashboardCommercialView(_MarketDashboardView):
    allowed_granularities = market_dashboard_commercial.COMMERCIAL_GRANULARITIES

    def build(self, period, granularity):
        return market_dashboard_commercial.build_commercial_dashboard(period, granularity)


class MarketDashboardDefinitionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _access_error(request)
        if denied is not None:
            return denied
        return Response(market_dashboards.definitions_payload())
