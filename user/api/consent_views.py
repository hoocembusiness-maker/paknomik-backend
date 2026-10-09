"""
JSON API for buyer consent (sharing contact details with a supplier).

Manages request → accept/refuse flow and billing. Buyer identity stays
hidden until they agree.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import crm
from user.entitlements import FEATURE_PAK_DATING, require_feature
from user.models import ConsentRecord, Opportunity
from user.pragmadinar import (
    get_service,
    hold_service,
    release_hold,
    settle_hold,
)
from user.platform_config import get_bool, get_int
from user.rbac import log_audit

User = get_user_model()

PAK_DATING_SERVICE_CODE = "commercial.pak_dating"

#: Only these buyer fields may ever be revealed, and only after consent.
REVEALABLE_FIELDS = ["company_name", "wilaya", "email", "phone_number", "full_name"]


def _pak_dating_enabled() -> bool:
    return get_bool("pak_dating_enabled", False)


def _anonymous_buyer_payload(record):
    """What a supplier sees while consent is pending or refused."""
    buyer = record.buyer
    return {
        "companyName": "Acheteur confidentiel",
        "wilaya": getattr(buyer, "wilaya", "") or "",
        "email": "",
        "phone": "",
        "fullName": "",
        "revealed": False,
    }


def _revealed_buyer_payload(record):
    buyer = record.buyer
    fields = record.revealed_fields or REVEALABLE_FIELDS
    payload = {"revealed": True}
    if "company_name" in fields:
        payload["companyName"] = getattr(buyer, "company_name", "") or ""
    if "wilaya" in fields:
        payload["wilaya"] = getattr(buyer, "wilaya", "") or ""
    if "email" in fields:
        payload["email"] = buyer.email
    if "phone_number" in fields:
        payload["phone"] = getattr(buyer, "phone_number", "") or ""
    if "full_name" in fields:
        payload["fullName"] = f"{buyer.first_name} {buyer.last_name}".strip()
    return payload


def _record_payload(record):
    base = {
        "id": record.id,
        "purpose": record.purpose,
        "purposeLabel": record.get_purpose_display(),
        "status": record.status,
        "statusLabel": record.get_status_display(),
        "signal": record.signal,
        "requestedAt": record.requested_at,
        "respondedAt": record.responded_at,
        "expiresAt": record.expires_at,
    }
    if record.status == ConsentRecord.Status.GRANTED:
        base["buyer"] = _revealed_buyer_payload(record)
    else:
        base["buyer"] = _anonymous_buyer_payload(record)
    return base


def _expire_stale(records_qs):
    """Release reservations for consent requests nobody answered."""
    now = timezone.now()
    stale = records_qs.filter(
        status=ConsentRecord.Status.PENDING, expires_at__lt=now
    )
    for record in stale:
        if record.hold_ledger_id:
            release_hold(record.hold_ledger, reason="Consentement expiré")
        record.status = ConsentRecord.Status.EXPIRED
        record.responded_at = now
        record.save(update_fields=["status", "responded_at"])


class PakDatingRequestView(APIView):
    """Supplier asks for an introduction. Debits nothing yet — only reserves."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        supplier = getattr(request.user, "supplier", None)
        if supplier is None:
            return Response(
                {"error": "Un compte fournisseur est requis.", "code": "supplier_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not _pak_dating_enabled():
            return Response(
                {
                    "error": "Le Pak-dating sera disponible avec Paknomik 2.0.",
                    "code": "feature_disabled",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        require_feature(supplier, FEATURE_PAK_DATING)

        buyer = User.objects.filter(pk=request.data.get("buyerId"), is_active=True).first()
        if buyer is None or buyer.supplier_id == supplier.id:
            return Response(
                {"error": "Acheteur introuvable."}, status=status.HTTP_404_NOT_FOUND
            )

        existing = ConsentRecord.objects.filter(
            supplier=supplier,
            buyer=buyer,
            purpose=ConsentRecord.Purpose.PAK_DATING,
            status=ConsentRecord.Status.PENDING,
        ).first()
        if existing:
            return Response(_record_payload(existing), status=status.HTTP_200_OK)

        ttl_hours = get_int("pak_dating_consent_ttl_hours", 72)
        with transaction.atomic():
            record = ConsentRecord.objects.create(
                buyer=buyer,
                supplier=supplier,
                purpose=ConsentRecord.Purpose.PAK_DATING,
                signal=(request.data.get("signal") or "")[:120],
                revealed_fields=REVEALABLE_FIELDS,
                expires_at=timezone.now() + timedelta(hours=ttl_hours),
            )
            hold = hold_service(
                supplier,
                PAK_DATING_SERVICE_CODE,
                ref_type="pak_dating",
                ref_id=record.id,
                reason="Pak-dating — réservation en attente de consentement",
                idempotency_key=f"pakdating:hold:{record.id}",
                actor=request.user,
            )
            record.hold_ledger = hold
            record.save(update_fields=["hold_ledger"])

        service = get_service(PAK_DATING_SERVICE_CODE)
        payload = _record_payload(record)
        payload["reserved"] = str(abs(hold.amount))
        payload["serviceLabel"] = service.label
        payload["notice"] = (
            "Le montant est réservé. Il ne sera débité que si l'acheteur accepte. "
            "En cas de refus ou d'expiration, la réservation est libérée."
        )
        return Response(payload, status=status.HTTP_201_CREATED)


class BuyerConsentListView(APIView):
    """Consent requests awaiting the buyer's decision."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ConsentRecord.objects.filter(buyer=request.user).select_related("supplier")
        if not _pak_dating_enabled():
            qs = qs.exclude(purpose=ConsentRecord.Purpose.PAK_DATING)
        _expire_stale(qs)
        results = []
        for record in qs.order_by("-requested_at")[:100]:
            results.append(
                {
                    "id": record.id,
                    "purpose": record.purpose,
                    "purposeLabel": record.get_purpose_display(),
                    "status": record.status,
                    "statusLabel": record.get_status_display(),
                    "supplierId": record.supplier_id,
                    "supplierName": record.supplier.company_name,
                    "signal": record.signal,
                    "requestedFields": record.revealed_fields,
                    "requestedAt": record.requested_at,
                    "expiresAt": record.expires_at,
                }
            )
        return Response({"results": results})


class BuyerConsentDecideView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        record = ConsentRecord.objects.filter(pk=pk, buyer=request.user).first()
        if record is None:
            return Response(
                {"error": "Demande introuvable."}, status=status.HTTP_404_NOT_FOUND
            )
        if record.status != ConsentRecord.Status.PENDING:
            return Response(
                {"error": "Cette demande a déjà été traitée.", "code": "already_decided"},
                status=status.HTTP_409_CONFLICT,
            )
        if (
            record.purpose == ConsentRecord.Purpose.PAK_DATING
            and not _pak_dating_enabled()
        ):
            return Response(
                {
                    "error": "Le Pak-dating sera disponible avec Paknomik 2.0.",
                    "code": "feature_disabled",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        decision = (request.data.get("decision") or "").strip().lower()
        if decision not in {"accept", "refuse"}:
            return Response(
                {"error": "Décision invalide."}, status=status.HTTP_400_BAD_REQUEST
            )

        now = timezone.now()
        with transaction.atomic():
            if decision == "accept":
                allowed = request.data.get("fields")
                if isinstance(allowed, list) and allowed:
                    record.revealed_fields = [
                        f for f in allowed if f in REVEALABLE_FIELDS
                    ]
                record.status = ConsentRecord.Status.GRANTED
                if record.hold_ledger_id:
                    settled = settle_hold(
                        record.hold_ledger,
                        reason="Pak-dating — consentement acheteur accordé",
                    )
                    record.charge_ledger = settled
            else:
                record.status = ConsentRecord.Status.REFUSED
                if record.hold_ledger_id:
                    release_hold(
                        record.hold_ledger, reason="Pak-dating — refus de l'acheteur"
                    )
            record.responded_at = now
            record.save(
                update_fields=[
                    "status",
                    "responded_at",
                    "revealed_fields",
                    "charge_ledger",
                ]
            )

            if record.status == ConsentRecord.Status.GRANTED:
                company, _ = crm.ensure_company(record.supplier, buyer=record.buyer)
                contact, _ = crm.ensure_contact(
                    record.supplier, company, buyer=record.buyer, consent=True
                )
                Opportunity.objects.get_or_create(
                    supplier=record.supplier,
                    company=company,
                    contact=contact,
                    source=Opportunity.Source.PAK_DATING,
                    stage=Opportunity.Stage.PROSPECT,
                    defaults={"title": f"Pak-dating — {company.name}"[:255]},
                )

        log_audit(
            request.user,
            f"consent.{record.status}",
            entity_type="ConsentRecord",
            entity_id=record.id,
            new_value={"status": record.status, "fields": record.revealed_fields},
            reason="Décision de l'acheteur sur une demande de consentement.",
            request=request,
        )
        return Response({"status": record.status})


class SupplierPakDatingListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier = getattr(request.user, "supplier", None)
        if supplier is None:
            return Response(
                {"error": "Un compte fournisseur est requis.", "code": "supplier_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        enabled = _pak_dating_enabled()
        qs = ConsentRecord.objects.filter(supplier=supplier).select_related("buyer")
        if not enabled:
            qs = qs.none()
        else:
            _expire_stale(qs)
        return Response(
            {
                "enabled": enabled,
                "results": [
                    _record_payload(r) for r in qs.order_by("-requested_at")[:100]
                ],
            }
        )
