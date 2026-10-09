"""
Creates chat messages when a supplier sends a proposal,
with a link to open the PDF.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model

from user.models import Message

User = get_user_model()


def resolve_buyer_for_rfq(rfq):
    if rfq.buyer_id:
        return rfq.buyer
    email = (rfq.email or "").strip().lower()
    if not email:
        return None
    return User.objects.filter(email__iexact=email).first()


def resolve_buyer_for_order(order):
    if order.buyer_id:
        return order.buyer
    return None


def proposal_document_path(*, kind: str, response_id: int) -> str:
    if kind == "order":
        return f"/api/proposals/orders/{response_id}/pdf/"
    return f"/api/proposals/quotes/{response_id}/pdf/"


def proposal_notice_content(*, supplier_name: str, kind: str) -> str:
    name = (supplier_name or "Le fournisseur").strip()
    if kind == "order":
        return f"{name} a répondu à votre commande."
    return f"{name} a répondu à votre demande de devis."


def create_proposal_notice_message(
    *,
    supplier,
    buyer,
    sender,
    kind: str,
    response_id: int,
) -> Message | None:
    if not buyer or not supplier or not sender:
        return None
    company = (getattr(supplier, "company_name", None) or "").strip()
    return Message.objects.create(
        supplier=supplier,
        buyer=buyer,
        sender=sender,
        content=proposal_notice_content(supplier_name=company, kind=kind),
        message_type=Message.MessageType.PROPOSAL,
        metadata={
            "proposalKind": kind,
            "responseId": response_id,
            "documentPath": proposal_document_path(kind=kind, response_id=response_id),
        },
        read_by_supplier=True,
        read_by_buyer=False,
    )
