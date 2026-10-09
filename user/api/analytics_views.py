"""Analytics and paid reports.

Reports are built first, then debited, then persisted — so a supplier is never
charged for output that could not be produced. Re-reading a report already bought
costs nothing. The current-month dashboard is billed per consultation (catalog).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import market_intel
from user.entitlements import (
    FEATURE_ADVANCED_REPORTS,
    FEATURE_BENCHMARKING,
    FEATURE_DASHBOARD_CURRENT_MONTH,
    FEATURE_DASHBOARD_HISTORY,
    FEATURE_MARKET_ANALYSIS,
    require_feature,
)
from user.models import MarketReport, PragmadinarService
from user.pragmadinar import (
    InsufficientPragmadinar,
    debit_service,
    format_cost_label,
    get_service,
    service_access_for_plan,
    get_supplier_plan_code,
    wallet_payload,
)
from user.platform_config import pragmadinar_to_dzd

#: Report code -> entitlement that must be unlocked for the plan.
REPORT_FEATURES = {
    market_intel.PREVIOUS_MONTH: FEATURE_DASHBOARD_HISTORY,
    market_intel.PERFORMANCE_COMPARE: FEATURE_DASHBOARD_HISTORY,
    market_intel.MARKET_OVERVIEW: FEATURE_MARKET_ANALYSIS,
    market_intel.MARKET_CATEGORY: FEATURE_MARKET_ANALYSIS,
    market_intel.MARKET_PERIOD: FEATURE_MARKET_ANALYSIS,
    market_intel.BENCHMARK_SUPPLIER: FEATURE_BENCHMARKING,
    market_intel.MARKET_PERFORMANCE: FEATURE_BENCHMARKING,
    market_intel.REPORT_DETAILED: FEATURE_ADVANCED_REPORTS,
    market_intel.REPORT_MONTHLY: FEATURE_ADVANCED_REPORTS,
    market_intel.REPORT_QUARTERLY: FEATURE_ADVANCED_REPORTS,
    market_intel.REPORT_SEMIANNUAL: FEATURE_ADVANCED_REPORTS,
    market_intel.REPORT_ANNUAL: FEATURE_ADVANCED_REPORTS,
}


def _supplier_or_denied(request):
    supplier = getattr(request.user, "supplier", None)
    if supplier is None:
        return None, Response(
            {"error": "Un compte fournisseur est requis.", "code": "supplier_required"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return supplier, None


def _report_summary(report):
    return {
        "id": report.id,
        "reportType": report.report_type,
        "title": report.title,
        "periodStart": report.period_start,
        "periodEnd": report.period_end,
        "costPragmadinar": str(report.cost_pragmadinar),
        "costDzd": str(pragmadinar_to_dzd(report.cost_pragmadinar)),
        "reference": report.ledger.reference if report.ledger_id else "",
        "createdAt": report.created_at,
    }


class AnalyticsCatalogView(APIView):
    """Purchasable reports with price, DA equivalent and current balance."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied

        plan = get_supplier_plan_code(supplier)
        wallet = wallet_payload(supplier, category="analytics")
        available = float(wallet["available"])

        services = {
            service.code: service
            for service in PragmadinarService.objects.filter(
                code__in=list(market_intel.REPORT_SPECS)
            )
        }
        catalog = []
        for code, (title, _months) in market_intel.REPORT_SPECS.items():
            service = services.get(code)
            if service is None or not service.is_active:
                continue
            cost = float(service.cost_pragmadinar)
            catalog.append(
                {
                    "code": code,
                    "title": title,
                    "serviceLabel": service.label,
                    "costPragmadinar": str(service.cost_pragmadinar),
                    "costDzd": str(pragmadinar_to_dzd(service.cost_pragmadinar)),
                    "costLabel": format_cost_label(
                        service.cost_pragmadinar,
                        service.unit,
                        service.unit_quantity,
                        service.unit_label,
                    ),
                    "access": service_access_for_plan(service, plan),
                    "affordable": available >= cost,
                    "balanceAfter": str(round(available - cost, 2)),
                    "needsPeriod": code == market_intel.MARKET_PERIOD,
                    "needsCategory": code == market_intel.MARKET_CATEGORY,
                }
            )

        return Response(
            {
                "plan": plan,
                "wallet": wallet,
                "currentMonthIsFree": False,
                "reports": catalog,
            }
        )


class AnalyticsReportListView(APIView):
    """Reports already paid for. Re-reading them is free."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        reports = MarketReport.objects.filter(supplier=supplier).select_related("ledger")
        return Response({"results": [_report_summary(r) for r in reports[:100]]})


class AnalyticsReportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied
        report = MarketReport.objects.filter(pk=pk, supplier=supplier).first()
        if report is None:
            return Response(
                {"error": "Rapport introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        payload = _report_summary(report)
        payload["params"] = report.params
        payload["payload"] = report.payload
        return Response(payload)


class AnalyticsReportPurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied

        report_type = (request.data.get("reportType") or "").strip()
        if report_type not in market_intel.REPORT_SPECS:
            return Response(
                {"error": "Type de rapport inconnu.", "code": "unknown_report"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        params = {
            key: request.data.get(key)
            for key in ("year", "month", "startDate", "endDate", "categoryId")
            if request.data.get(key) not in (None, "")
        }

        try:
            require_feature(supplier, REPORT_FEATURES[report_type])
        except PermissionDenied as exc:
            return Response(exc.detail, status=status.HTTP_403_FORBIDDEN)

        # Build before debiting: an impossible report must never cost a Pragmadinar.
        try:
            payload = market_intel.build_report(supplier, report_type, params)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)

        period = payload["period"]
        signature = "|".join(
            [report_type, period["start"], period["end"], str(params.get("categoryId") or "")]
        )
        existing = MarketReport.objects.filter(
            supplier=supplier, report_type=report_type, params=params
        ).first()
        if existing is not None:
            # Already paid for this exact report: return it without charging.
            result = _report_summary(existing)
            result["payload"] = existing.payload
            result["alreadyOwned"] = True
            return Response(result)

        service = get_service(report_type)
        try:
            ledger = debit_service(
                supplier,
                report_type,
                ref_type="market_report",
                reason=f"{payload['title']} ({period['start']} → {period['end']})",
                idempotency_key=f"report:{supplier.pk}:{signature}",
                actor=request.user,
            )
        except InsufficientPragmadinar as exc:
            return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)
        except PermissionDenied as exc:
            return Response(exc.detail, status=status.HTTP_403_FORBIDDEN)

        report = MarketReport.objects.create(
            supplier=supplier,
            service=service,
            report_type=report_type,
            title=payload["title"],
            params=params,
            payload=payload,
            period_start=period["start"],
            period_end=period["end"],
            ledger=ledger,
            cost_pragmadinar=abs(ledger.amount),
        )

        result = _report_summary(report)
        result["payload"] = payload
        result["alreadyOwned"] = False
        result["wallet"] = wallet_payload(supplier, category="analytics")
        return Response(result, status=status.HTTP_201_CREATED)


class AnalyticsCurrentMonthView(APIView):
    """Current month dashboard — billed via intel.dashboard_current (catalog)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier, denied = _supplier_or_denied(request)
        if denied:
            return denied

        from django.utils import timezone

        now = timezone.now()
        service_code = "intel.dashboard_current"
        idempotency_key = (
            f"dashboard_current:{supplier.pk}:{now.year}:{now.month}:{now.day}"
        )
        try:
            debit_service(
                supplier,
                service_code,
                ref_type="dashboard_current",
                reason="Tableau de bord de mois en cours",
                idempotency_key=idempotency_key,
                actor=request.user,
            )
        except InsufficientPragmadinar as exc:
            return Response(exc.detail, status=status.HTTP_402_PAYMENT_REQUIRED)
        except PermissionDenied as exc:
            return Response(exc.detail, status=status.HTTP_403_FORBIDDEN)

        payload = market_intel.build_report(
            supplier,
            market_intel.REPORT_DETAILED,
            {"year": now.year, "month": now.month},
        )
        payload["free"] = False
        payload["title"] = "Tableau de bord de mois en cours"
        payload["wallet"] = wallet_payload(supplier, category="analytics")
        return Response(payload)
