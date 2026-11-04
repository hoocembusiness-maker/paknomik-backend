"""Buyer and supplier APIs for packaging-specific quote forms."""

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user.api.serializers import SupplierRfqSerializer
from user.models import Category, RFQRequest, Supplier
from user.quote_forms import (
    QuoteFormError,
    change_rfq_status,
    offered_category_payloads,
    resolve_buyer_form,
    save_supplier_form,
    supplier_form_payload,
)


def _error(exc):
    return Response({"error": exc.message}, status=exc.status_code)


def _active_supplier(supplier_id):
    return Supplier.objects.filter(pk=supplier_id, is_active=True, is_ghost=False).first()


class BuyerQuoteFormView(APIView):
    """Resolved technical form for one supplier and one packaging type."""

    def get(self, request):
        supplier_id = request.query_params.get("supplierId")
        category_id = request.query_params.get("categoryId")
        if not (str(supplier_id or "").isdigit() and str(category_id or "").isdigit()):
            return Response(
                {"error": "supplierId et categoryId sont requis."},
                status=400,
            )
        supplier = _active_supplier(int(supplier_id))
        if supplier is None:
            return Response({"error": "Fournisseur introuvable."}, status=404)
        category = Category.objects.filter(pk=int(category_id)).first()
        if category is None:
            return Response({"error": "Type d'emballage introuvable."}, status=404)
        try:
            return Response(resolve_buyer_form(supplier, category))
        except QuoteFormError as exc:
            return _error(exc)


class SupplierQuoteFormListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.supplier_id:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        supplier = request.user.supplier
        return Response({"categories": offered_category_payloads(supplier)})


class SupplierQuoteFormDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _category(self, request, category_id):
        if not request.user.supplier_id:
            return None, Response({"error": "Un compte fournisseur est requis."}, status=403)
        category = get_object_or_404(Category, pk=category_id)
        return category, None

    def get(self, request, category_id):
        category, denied = self._category(request, category_id)
        if denied:
            return denied
        try:
            return Response(supplier_form_payload(request.user.supplier, category))
        except QuoteFormError as exc:
            return _error(exc)

    def put(self, request, category_id):
        category, denied = self._category(request, category_id)
        if denied:
            return denied
        try:
            payload = save_supplier_form(request.user.supplier, category, request.data)
        except QuoteFormError as exc:
            return _error(exc)
        return Response(payload)


class SupplierRfqDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not request.user.supplier_id:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        rfq = (
            RFQRequest.objects.filter(pk=pk, supplier_id=request.user.supplier_id)
            .select_related("product", "category")
            .prefetch_related("responses", "photos", "files", "events")
            .first()
        )
        if rfq is None:
            return Response({"error": "Demande introuvable."}, status=404)
        data = SupplierRfqSerializer(rfq, context={"request": request}).data
        data["events"] = [
            {
                "action": event.action,
                "actionLabel": event.get_action_display(),
                "comment": event.comment,
                "createdAt": event.created_at.isoformat(),
            }
            for event in rfq.events.all()
        ]
        return Response(data)


class SupplierRfqStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not request.user.supplier_id:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        rfq = RFQRequest.objects.filter(
            pk=pk, supplier_id=request.user.supplier_id
        ).first()
        if rfq is None:
            return Response({"error": "Demande introuvable."}, status=404)
        new_status = (request.data.get("status") or "").strip()
        comment = (request.data.get("comment") or "").strip()
        if len(comment) > 2000:
            return Response({"error": "Le commentaire est trop long."}, status=400)
        try:
            change_rfq_status(rfq, new_status, request.user, comment)
        except QuoteFormError as exc:
            return _error(exc)
        return Response(SupplierRfqSerializer(rfq, context={"request": request}).data)
