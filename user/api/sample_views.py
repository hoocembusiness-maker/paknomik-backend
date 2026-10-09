"""Sample requests (« Demande d'échantillons ») sent from a supplier boutique.

Always free: no Pragmadinar debit for the buyer or the supplier.
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import crm
from user.api.serializers import SupplierSampleRequestSerializer
from user.models import (
    AppNotification,
    Message,
    Product,
    RFQRequest,
    SampleRequest,
    SampleRequestPhoto,
    Supplier,
)
from user.photo_uploads import clean_photos, sanitized_photos, save_photos
from user.samples import sample_settings

logger = logging.getLogger(__name__)

SUPPLIER_STATUS_TRANSITIONS = {
    RFQRequest.Status.SUBMITTED: {RFQRequest.Status.IN_REVIEW, RFQRequest.Status.REJECTED},
    RFQRequest.Status.IN_REVIEW: {RFQRequest.Status.ACCEPTED, RFQRequest.Status.REJECTED},
}


def _notify_supplier(sample):
    product = f" ({sample.product.name})" if sample.product_id and sample.product else ""
    for user in sample.supplier.users.all():
        AppNotification.objects.create(
            user=user,
            kind=AppNotification.Kind.SAMPLE_REQUEST,
            title="Nouvelle demande d'échantillon",
            body=f"{sample.name} demande {sample.quantity} échantillons{product}.",
            link="/supplier/quotes?view=samples",
        )


class SampleRequestCreateView(APIView):
    """Public: anonymous buyers give a phone number, signed-in buyers their e-mail."""

    def post(self, request):
        data = request.data
        name = str(data.get("name") or "").strip()
        company = str(data.get("company") or "").strip()
        email = str(data.get("email") or "").strip().lower()
        phone = str(data.get("phone") or "").strip()
        delivery_address = str(data.get("deliveryAddress") or "").strip()
        message = str(data.get("message") or "").strip()

        buyer = request.user if request.user.is_authenticated else None
        has_contact = email if buyer else phone
        if not name or not has_contact or not delivery_address:
            return Response(
                {"error": "Nom, coordonnées et adresse de livraison sont obligatoires."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        supplier = Supplier.objects.filter(pk=data.get("supplierId"), is_active=True).first()
        if supplier is None:
            return Response({"error": "Fournisseur introuvable."}, status=status.HTTP_404_NOT_FOUND)
        settings = sample_settings(supplier)
        if not settings["enabled"]:
            return Response(
                {"error": "Ce fournisseur ne propose pas d'échantillons."},
                status=status.HTTP_403_FORBIDDEN,
            )

        photos = request.FILES.getlist("photos")
        photo_error = clean_photos(photos)
        if photo_error:
            return Response({"error": photo_error}, status=status.HTTP_400_BAD_REQUEST)
        photos = sanitized_photos(photos)

        product = None
        if data.get("productId"):
            product = Product.objects.filter(
                pk=data.get("productId"), supplier=supplier, is_active=True
            ).first()

        sample = SampleRequest.objects.create(
            buyer=buyer,
            supplier=supplier,
            product=product,
            name=name[:255],
            company=company[:255],
            email=email if buyer else "",
            phone="" if buyer else phone[:32],
            delivery_address=delivery_address[:500],
            message=message,
            quantity=settings["quantity"],
        )
        save_photos(SampleRequestPhoto, "sample_request", sample, photos)

        if buyer:
            product_label = f" pour « {product.name} »" if product else ""
            Message.objects.create(
                supplier=supplier,
                buyer=buyer,
                sender=buyer,
                content=message
                or f"Demande de {sample.quantity} échantillons{product_label}.",
                read_by_buyer=True,
                read_by_supplier=False,
            )
        try:
            crm.sync_from_sample_request(sample)
        except Exception:
            logger.exception("CRM sync failed for sample request %s", sample.pk)
        _notify_supplier(sample)

        return Response(
            {
                "success": True,
                "id": sample.pk,
                "quantity": sample.quantity,
                "message": f"Votre demande d'échantillon a été envoyée à {supplier.company_name}.",
            },
            status=status.HTTP_201_CREATED,
        )


def _supplier_or_denied(request):
    if not request.user.supplier_id:
        return None, Response(
            {"error": "Compte fournisseur requis."}, status=status.HTTP_403_FORBIDDEN
        )
    return request.user.supplier_id, None


class SupplierSampleRequestListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier_id, denied = _supplier_or_denied(request)
        if denied:
            return denied
        qs = (
            SampleRequest.objects.filter(supplier_id=supplier_id)
            .select_related("product")
            .prefetch_related("photos")
        )
        return Response(
            SupplierSampleRequestSerializer(qs, many=True, context={"request": request}).data
        )


class SupplierSampleRequestStatusView(APIView):
    """Move a sample request along the boutique request statuses."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        supplier_id, denied = _supplier_or_denied(request)
        if denied:
            return denied
        sample = SampleRequest.objects.filter(pk=pk, supplier_id=supplier_id).first()
        if sample is None:
            return Response({"error": "Demande introuvable."}, status=status.HTTP_404_NOT_FOUND)
        target = str(request.data.get("status") or "")
        if target not in SUPPLIER_STATUS_TRANSITIONS.get(sample.status, set()):
            return Response(
                {"error": "Ce changement de statut n'est pas possible."},
                status=status.HTTP_409_CONFLICT,
            )
        sample.status = target
        update_fields = ["status", "updated_at"]
        if sample.responded_at is None:
            sample.responded_at = timezone.now()
            update_fields.append("responded_at")
        sample.save(update_fields=update_fields)
        return Response(SupplierSampleRequestSerializer(sample, context={"request": request}).data)
