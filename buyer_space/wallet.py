"""Buyer dossier and append-only Pragmadinar journal."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from buyer_space.constants import (
    COMPANY_GRANT_AMOUNT,
    COMPANY_GRANT_KEY,
    COMPANY_GRANT_SERVICES,
)
from buyer_space.models import (
    BuyerAccount,
    BuyerPragmadinarEntry,
    ServiceAvailability,
    ServiceRequest,
)


class WalletError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def ensure_buyer_account(user) -> BuyerAccount:
    account, _created = BuyerAccount.objects.get_or_create(user=user)
    return account


def grant_company_registration(user) -> bool:
    """Credit 50 Pragmadinars once, spendable only on Paknoma Design and Paklytik."""
    if getattr(user, "supplier_id", None):
        return False
    if not (user.company_name or "").strip():
        return False
    account = ensure_buyer_account(user)
    if account.entries.filter(idempotency_key=COMPANY_GRANT_KEY).exists():
        return False
    BuyerPragmadinarEntry.objects.create(
        account=account,
        amount=COMPANY_GRANT_AMOUNT,
        entry_type=BuyerPragmadinarEntry.EntryType.COMPANY_GRANT,
        eligible_services=list(COMPANY_GRANT_SERVICES),
        idempotency_key=COMPANY_GRANT_KEY,
        reason="Enregistrement d'entreprise",
    )
    return True


def _credit_remaining(credit: BuyerPragmadinarEntry) -> Decimal:
    used = credit.consumptions.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    # consumptions are stored as negative amounts
    return credit.amount + used


def service_charge_amount(
    account: BuyerAccount, service_type: str, availability: ServiceAvailability
) -> Decimal | None:
    """None: no tariff is configured. Zero: the request is still inside the free quota."""
    if availability.unit_cost is None:
        return None
    prior = ServiceRequest.objects.filter(
        account=account, service_type=service_type
    ).count()
    free = availability.free_quota or 0
    if prior < free:
        return Decimal("0.00")
    return Decimal(availability.unit_cost)


def remaining_for(account: BuyerAccount, service_type: str) -> Decimal:
    total = Decimal("0")
    credits = account.entries.filter(amount__gt=0).prefetch_related("consumptions")
    for credit in credits:
        allowed = credit.eligible_services or []
        if allowed and service_type not in allowed:
            continue
        total += _credit_remaining(credit)
    return total


@transaction.atomic
def debit_for_service(
    account: BuyerAccount,
    service_type: str,
    amount: Decimal,
    *,
    reason: str,
    idempotency_key: str,
    service_request: ServiceRequest | None = None,
) -> BuyerPragmadinarEntry:
    amount = Decimal(amount)
    if amount <= 0:
        raise WalletError("Le montant à débiter doit être positif.")
    existing = account.entries.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    account = BuyerAccount.objects.select_for_update().get(pk=account.pk)
    credit = None
    for candidate in account.entries.select_for_update().filter(amount__gt=0).order_by("id"):
        allowed = candidate.eligible_services or []
        if allowed and service_type not in allowed:
            continue
        if _credit_remaining(candidate) >= amount:
            credit = candidate
            break
    if credit is None:
        raise WalletError("Solde Pragmadinar insuffisant pour ce service.")
    return BuyerPragmadinarEntry.objects.create(
        account=account,
        amount=-amount,
        entry_type=BuyerPragmadinarEntry.EntryType.SPEND,
        eligible_services=[service_type],
        consumed_entry=credit,
        service_request=service_request,
        idempotency_key=idempotency_key,
        reason=reason,
    )
