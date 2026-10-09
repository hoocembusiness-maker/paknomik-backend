"""Export reference data, commission tracking and supplier mandates.

Commissions are visible to the supplier concerned and to administrators holding
the commission permissions — never to buyers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import rbac
from user.api.rbac_views import PermissionScopedView
from user.commission import (
    rate_bounds,
    rate_within_bounds,
    review_commission,
    serialize_commission,
    serialize_mandate,
)
from user.export_terms import export_options_payload
from user.models import OrderCommission, Supplier, SupplierMandate

PAGE_SIZE = 100


class ExportOptionsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(export_options_payload())


class SupplierCommissionListView(APIView):
    """The supplier's own commission lines (read-only)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier_id = getattr(request.user, "supplier_id", None)
        if not supplier_id:
            return Response(
                {"error": "Réservé aux fournisseurs."}, status=status.HTTP_403_FORBIDDEN
            )
        rows = (
            OrderCommission.objects.filter(order__supplier_id=supplier_id)
            .select_related("order", "mandate")[:PAGE_SIZE]
        )
        return Response({"results": [serialize_commission(row) for row in rows]})


class AdminCommissionListView(PermissionScopedView):
    required_permission = rbac.COMMISSIONS_READ

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        qs = OrderCommission.objects.select_related(
            "order", "order__supplier", "mandate"
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        supplier_id = request.query_params.get("supplierId")
        if supplier_id:
            qs = qs.filter(order__supplier_id=supplier_id)
        scope = request.query_params.get("tradeScope")
        if scope:
            qs = qs.filter(trade_scope=scope)
        low, high = rate_bounds()
        return Response(
            {
                "rateBounds": {
                    "min": str(low) if low is not None else None,
                    "max": str(high) if high is not None else None,
                },
                "results": [
                    serialize_commission(row, include_admin=True) for row in qs[:PAGE_SIZE]
                ],
            }
        )


class AdminCommissionDetailView(PermissionScopedView):
    required_permission = rbac.COMMISSIONS_MANAGE

    def patch(self, request, pk):
        denied = self.check(request)
        if denied:
            return denied
        commission = OrderCommission.objects.filter(pk=pk).select_related("order").first()
        if commission is None:
            return Response({"error": "Commission introuvable."}, status=status.HTTP_404_NOT_FOUND)
        try:
            commission = review_commission(commission, request.user, request.data, request=request)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        commission = OrderCommission.objects.select_related(
            "order", "order__supplier", "mandate"
        ).get(pk=commission.pk)
        return Response(serialize_commission(commission, include_admin=True))


def _parse_date(value, field):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValidationError(
            {"error": f"Date invalide ({field}).", "code": "invalid_date"}
        ) from exc


def _apply_mandate_data(mandate, data, *, creating: bool):
    if creating or "commissionRate" in data:
        try:
            rate = Decimal(str(data.get("commissionRate")))
        except (InvalidOperation, TypeError) as exc:
            raise ValidationError(
                {"error": "Taux de commission invalide.", "code": "invalid_rate"}
            ) from exc
        if rate <= 0 or rate >= 100:
            raise ValidationError({"error": "Taux de commission invalide.", "code": "invalid_rate"})
        if not rate_within_bounds(rate):
            low, high = rate_bounds()
            raise ValidationError(
                {
                    "error": f"Le taux doit être compris entre {low} et {high} %.",
                    "code": "rate_out_of_range",
                }
            )
        mandate.commission_rate = rate
    for key, field, choices in (
        ("scope", "scope", SupplierMandate.Scope.values),
        ("status", "status", SupplierMandate.Status.values),
        ("calculationBase", "calculation_base", SupplierMandate.CalculationBase.values),
    ):
        if key in data:
            if data[key] not in choices:
                raise ValidationError(
                    {"error": f"Valeur invalide ({key}).", "code": "invalid_value"}
                )
            setattr(mandate, field, data[key])
    for key, field, max_len in (
        ("reference", "reference", 64),
        ("calculationTerms", "calculation_terms", 4000),
        ("notes", "notes", 4000),
    ):
        if key in data:
            setattr(mandate, field, str(data.get(key) or "").strip()[:max_len])
    if "startsOn" in data:
        mandate.starts_on = _parse_date(data.get("startsOn"), "startsOn")
    if "endsOn" in data:
        mandate.ends_on = _parse_date(data.get("endsOn"), "endsOn")
    if mandate.starts_on and mandate.ends_on and mandate.ends_on < mandate.starts_on:
        raise ValidationError(
            {"error": "La date de fin précède la date de début.", "code": "invalid_period"}
        )


class AdminMandateListView(PermissionScopedView):
    required_permission = rbac.COMMISSIONS_READ

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        qs = SupplierMandate.objects.select_related("supplier")
        supplier_id = request.query_params.get("supplierId")
        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)
        return Response({"results": [serialize_mandate(m) for m in qs[:PAGE_SIZE]]})

    def post(self, request):
        denied = self.check(request, rbac.COMMISSIONS_MANAGE)
        if denied:
            return denied
        supplier = Supplier.objects.filter(pk=request.data.get("supplierId")).first()
        if supplier is None:
            return Response(
                {"error": "Fournisseur introuvable."}, status=status.HTTP_400_BAD_REQUEST
            )
        mandate = SupplierMandate(supplier=supplier, created_by=request.user)
        try:
            _apply_mandate_data(mandate, request.data, creating=True)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        mandate.save()
        rbac.log_audit(
            request.user,
            "mandate.create",
            entity_type="supplier_mandate",
            entity_id=mandate.pk,
            new_value=serialize_mandate(mandate),
            request=request,
        )
        return Response(serialize_mandate(mandate), status=status.HTTP_201_CREATED)


class AdminMandateDetailView(PermissionScopedView):
    required_permission = rbac.COMMISSIONS_MANAGE

    def patch(self, request, pk):
        denied = self.check(request)
        if denied:
            return denied
        mandate = SupplierMandate.objects.select_related("supplier").filter(pk=pk).first()
        if mandate is None:
            return Response({"error": "Mandat introuvable."}, status=status.HTTP_404_NOT_FOUND)
        before = serialize_mandate(mandate)
        try:
            _apply_mandate_data(mandate, request.data, creating=False)
        except ValidationError as exc:
            return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
        mandate.save()
        rbac.log_audit(
            request.user,
            "mandate.update",
            entity_type="supplier_mandate",
            entity_id=mandate.pk,
            old_value=before,
            new_value=serialize_mandate(mandate),
            request=request,
        )
        return Response(serialize_mandate(mandate))
