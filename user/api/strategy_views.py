from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import campaigns
from user.models import (
    PragmadinarService,
    PragmadinarStrategyReservation,
    PragmadinarWallet,
    Supplier,
)
from user.pragmadinar import (
    CATALOG_SERVICES,
    UNBILLED_SERVICE_CODES,
    WALLET_CATEGORIES,
    format_cost_label,
    format_pragmadinar_amount,
    get_or_create_wallet,
    get_service,
    hold_service,
    reallocate_wallet_distribution,
    release_hold,
    strategy_wallet_for_code,
    tariff_display_cost,
    transfer_wallet_balance,
    wallet_payload,
)


WALLET_LABELS = {
    "visibility": "Visibilité",
    "analytics": "Analytics",
    "promotion": "Promotion",
}
RHYTHMS = {choice for choice, _label in PragmadinarStrategyReservation.Rhythm.choices}
REMINDER_INTERVAL = timedelta(hours=36)


def _supplier_for(request):
    supplier_id = getattr(request.user, "supplier_id", None)
    return Supplier.objects.filter(pk=supplier_id).first() if supplier_id else None


def _error(exc, http_status=status.HTTP_400_BAD_REQUEST):
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return Response(detail, status=http_status)
    return Response({"error": str(detail or exc)}, status=http_status)


def _release_expired_reservations(supplier):
    now = timezone.now()
    expired = PragmadinarStrategyReservation.objects.filter(
        supplier=supplier,
        status=PragmadinarStrategyReservation.Status.RESERVED,
        ends_at__lte=now,
    ).select_related("hold_ledger")
    for plan in expired:
        if plan.hold_ledger_id:
            release_hold(plan.hold_ledger, reason="Fin de la période planifiée")
        plan.status = PragmadinarStrategyReservation.Status.RELEASED
        plan.save(update_fields=["status", "updated_at"])


_CATALOG_PRICES = {
    row["code"]: Decimal(str(row["cost_pragmadinar"])) for row in CATALOG_SERVICES
}


def _planning_price(service):
    """Price shown in the strategy tool.

    Unbilled visibility services keep a catalog price of 0 in the database so
    live usage does not debit. The strategy still lists them at the catalog rate.
    """
    if service.cost_pragmadinar > 0:
        return service.cost_pragmadinar, False
    listed = _CATALOG_PRICES.get(service.code, Decimal("0"))
    if listed > 0 and service.code in UNBILLED_SERVICE_CODES:
        return listed, True
    return Decimal("0"), False


def _quantity_for_amount(service, amount):
    unit = tariff_display_cost(service)
    if unit <= 0:
        return 1
    return max(int((Decimal(amount) / unit).to_integral_value(ROUND_FLOOR)), 1)


def _shift_hold(plan, delta):
    from user.models import PragmadinarLedger, PragmadinarWallet

    hold = plan.hold_ledger
    if hold is None or hold.status != PragmadinarLedger.Status.HELD:
        return
    wallet = PragmadinarWallet.objects.select_for_update().get(pk=hold.wallet_id)
    wallet.held_total = max(wallet.held_total + delta, Decimal("0"))
    wallet.save(update_fields=["held_total", "updated_at"])
    hold.amount = -(abs(hold.amount) + delta)
    hold.save(update_fields=["amount"])


def _reservation_cost(service, quantity):
    """Plan the catalog price, including services stored at 0 so live usage stays free."""
    unit_price = tariff_display_cost(service)
    unit_quantity = max(int(service.unit_quantity or 1), 1)
    billable_units = (Decimal(quantity) / Decimal(unit_quantity)).quantize(
        Decimal("1"), rounding=ROUND_CEILING
    )
    return (unit_price * billable_units).quantize(Decimal("0.01"))


def _service_option(service, *, key, label, wallet_category, campaign_format):
    price, included = _planning_price(service)
    if price <= 0:
        return None
    return {
        "key": key,
        "serviceCode": service.code,
        "label": label,
        "walletCategory": wallet_category,
        "unit": service.unit,
        "unitQuantity": service.unit_quantity,
        "costPragmadinar": str(price),
        "costLabel": format_cost_label(
            price,
            service.unit,
            service.unit_quantity,
            service.unit_label,
        ),
        "campaignFormat": campaign_format,
        "included": included,
    }


def _service_options():
    """Objectives follow the supplier price list: same services, labels and class."""
    options = []
    by_code = {
        service.code: service
        for service in PragmadinarService.objects.filter(is_active=True)
    }
    catalog_labels = {row["code"]: row["label"] for row in CATALOG_SERVICES}
    for catalog_row in CATALOG_SERVICES:
        wallet_category = strategy_wallet_for_code(catalog_row["code"])
        service = by_code.get(catalog_row["code"])
        if not wallet_category or service is None:
            continue
        option = _service_option(
            service,
            key=service.code,
            label=catalog_labels.get(service.code, service.label),
            wallet_category=wallet_category,
            campaign_format=None,
        )
        if option:
            options.append(option)
    return options


def _serialize_reservation(plan):
    return {
        "id": plan.id,
        "serviceCode": plan.service.code,
        "serviceLabel": plan.service.label,
        "walletCategory": plan.wallet.category,
        "walletLabel": WALLET_LABELS.get(plan.wallet.category, plan.wallet.category),
        "targetQuantity": plan.target_quantity,
        "period": plan.period,
        "startDate": plan.start_date.isoformat() if plan.start_date else None,
        "endDate": plan.end_date.isoformat() if plan.end_date else None,
        "rhythm": plan.rhythm,
        "rhythmDetail": plan.rhythm_detail,
        "reservedAmount": format_pragmadinar_amount(plan.reserved_amount),
        "status": plan.status,
        "endsAt": plan.ends_at.isoformat() if plan.ends_at else None,
        "createdAt": plan.created_at.isoformat(),
    }


class SupplierPragmadinarStrategyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        _release_expired_reservations(supplier)
        wallets = wallet_payload(supplier)
        services = _service_options()
        available_by_category = {
            item["category"]: Decimal(item["available"])
            for item in wallets["wallets"]
        }
        for option in services:
            price = Decimal(option["costPragmadinar"])
            available = available_by_category.get(option["walletCategory"], Decimal("0"))
            option["availablePragmadinar"] = format_pragmadinar_amount(available)
            option["achievableQuantity30Days"] = int(
                (available / price * max(int(option["unitQuantity"]), 1)).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )

        reservations = PragmadinarStrategyReservation.objects.filter(
            supplier=supplier
        ).select_related("service", "wallet")[:30]
        return Response(
            {
                "wallet": wallets,
                "walletCategories": [
                    {"key": category, "label": WALLET_LABELS[category]}
                    for category in WALLET_CATEGORIES
                ],
                "services": services,
                "reservations": [_serialize_reservation(plan) for plan in reservations],
                "strategyToolVisits": supplier.strategy_tool_visits,
            }
        )

    @transaction.atomic
    def post(self, request):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        category = request.data.get("walletCategory")
        service_code = request.data.get("serviceCode")
        try:
            target_quantity = int(request.data.get("targetQuantity", 0))
        except (TypeError, ValueError):
            target_quantity = 0
        campaign_format = request.data.get("campaignFormat")
        rhythm = request.data.get("rhythm", PragmadinarStrategyReservation.Rhythm.VARIABLE)
        rhythm_detail = str(request.data.get("rhythmDetail") or "").strip()[:100]
        open_ended = bool(request.data.get("openEnded"))
        start_date = parse_date(str(request.data.get("startDate") or ""))
        end_date = None if open_ended else parse_date(str(request.data.get("endDate") or ""))

        if category not in WALLET_CATEGORIES:
            return Response({"error": "Portefeuille invalide."}, status=400)
        if target_quantity <= 0:
            return Response({"error": "Indiquez un objectif positif."}, status=400)
        if rhythm not in RHYTHMS:
            return Response({"error": "Sélectionnez un rythme de planification valide."}, status=400)
        if open_ended and not start_date:
            start_date = timezone.localdate()
        if not start_date:
            return Response({"error": "Indiquez une date de début."}, status=400)
        if start_date < timezone.localdate():
            return Response({"error": "La date de début ne peut pas être passée."}, status=400)
        if open_ended:
            period = PragmadinarStrategyReservation.Period.OPEN
            end_at = None
        else:
            if not end_date or end_date < start_date:
                return Response({"error": "Indiquez une plage de dates valide."}, status=400)
            if (end_date - start_date).days > 365:
                return Response({"error": "La plage de planification ne peut pas dépasser un an."}, status=400)
            duration_days = (end_date - start_date).days + 1
            period = "day" if duration_days == 1 else "week" if duration_days <= 7 else "month"
            end_at = timezone.make_aware(
                datetime.combine(end_date + timedelta(days=1), time.min),
                timezone.get_current_timezone(),
            )
        if rhythm == PragmadinarStrategyReservation.Rhythm.WEEKDAYS:
            if not rhythm_detail or any(day not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"} for day in rhythm_detail.split(",")):
                return Response({"error": "Sélectionnez au moins un jour de la semaine."}, status=400)
        if rhythm in {
            PragmadinarStrategyReservation.Rhythm.DAILY_LIMIT,
            PragmadinarStrategyReservation.Rhythm.WEEKLY_LIMIT,
        } and (not rhythm_detail.isdigit() or int(rhythm_detail) <= 0):
            return Response({"error": "Indiquez un plafond positif pour ce rythme."}, status=400)
        if campaign_format:
            if campaigns.FORMAT_SERVICE.get(campaign_format) != service_code or category != "promotion":
                return Response({"error": "Objectif de promotion invalide."}, status=400)

        try:
            service = get_service(service_code)
        except ValidationError as exc:
            return _error(exc)
        is_campaign_objective = bool(campaign_format)
        if strategy_wallet_for_code(service.code) != category and not is_campaign_objective:
            return Response({"error": "Cet objectif ne correspond pas à ce portefeuille."}, status=400)
        cost = _reservation_cost(service, target_quantity)
        if cost <= 0:
            return Response({"error": "Cet objectif n’a pas de tarif réservable."}, status=400)
        wallet = get_or_create_wallet(supplier, category=category)
        plan = PragmadinarStrategyReservation.objects.create(
            supplier=supplier,
            wallet=wallet,
            service=service,
            target_quantity=target_quantity,
            period=period,
            start_date=start_date,
            end_date=end_date,
            rhythm=rhythm,
            rhythm_detail=rhythm_detail,
            reserved_amount=cost,
            ends_at=end_at,
        )
        try:
            hold = hold_service(
                supplier,
                service_code,
                amount=cost,
                wallet_category=category,
                ref_type="strategy_plan",
                ref_id=plan.pk,
                reason=f"Plan Pragmadinar — {target_quantity} unités",
                idempotency_key=f"strategy-plan:{plan.pk}",
                actor=request.user,
            )
        except ValidationError as exc:
            plan.delete()
            return _error(exc)
        if hold is None:
            plan.delete()
            return Response({"error": "Ce service ne peut pas être réservé."}, status=400)
        plan.hold_ledger = hold
        plan.save(update_fields=["hold_ledger", "updated_at"])
        return Response(
            {
                "reservation": _serialize_reservation(plan),
                "wallet": wallet_payload(supplier),
            },
            status=status.HTTP_201_CREATED,
        )


class SupplierPragmadinarWalletTransferView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        try:
            result = transfer_wallet_balance(
                supplier,
                request.data.get("fromCategory"),
                request.data.get("toCategory"),
                request.data.get("amount"),
                actor=request.user,
            )
        except ValidationError as exc:
            return _error(exc)
        return Response({"wallet": result})


class SupplierPragmadinarWalletDistributionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        try:
            result = reallocate_wallet_distribution(
                supplier, request.data.get("allocations") or {}, actor=request.user
            )
        except ValidationError as exc:
            return _error(exc)
        return Response({"wallet": result})


class SupplierPragmadinarReservationReallocateView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        try:
            from_id = int(request.data.get("fromId"))
            to_id = int(request.data.get("toId"))
            amount = Decimal(str(request.data.get("amount") or "0")).quantize(Decimal("0.01"))
        except (TypeError, ValueError, ArithmeticError):
            return Response({"error": "Indiquez deux services et un montant."}, status=400)
        if from_id == to_id or amount <= 0:
            return Response({"error": "Choisissez deux services et un montant positif."}, status=400)
        plans = {
            plan.pk: plan
            for plan in PragmadinarStrategyReservation.objects.select_for_update()
            .filter(
                supplier=supplier,
                pk__in=(from_id, to_id),
                status=PragmadinarStrategyReservation.Status.RESERVED,
            )
            .select_related("hold_ledger", "wallet", "service")
        }
        source = plans.get(from_id)
        target = plans.get(to_id)
        if source is None or target is None:
            return Response({"error": "Réservation introuvable."}, status=404)
        if source.wallet_id != target.wallet_id:
            return Response({"error": "Les deux services doivent appartenir au même portefeuille."}, status=400)
        if amount > source.reserved_amount:
            return Response({"error": "Le montant dépasse le solde réservé de ce service."}, status=400)
        emptying = amount == source.reserved_amount
        _shift_hold(target, amount)
        if emptying:
            if source.hold_ledger_id:
                release_hold(source.hold_ledger, reason="Solde réaffecté vers un autre service")
            source.status = PragmadinarStrategyReservation.Status.RELEASED
            source.reserved_amount = Decimal("0")
            source.save(update_fields=["status", "reserved_amount", "updated_at"])
        else:
            _shift_hold(source, -amount)
            source.reserved_amount -= amount
            source.target_quantity = _quantity_for_amount(source.service, source.reserved_amount)
            source.save(update_fields=["reserved_amount", "target_quantity", "updated_at"])
        target.reserved_amount += amount
        target.target_quantity = _quantity_for_amount(target.service, target.reserved_amount)
        target.save(update_fields=["reserved_amount", "target_quantity", "updated_at"])
        return Response({"wallet": wallet_payload(supplier)})


class SupplierPragmadinarReservationReleaseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        plan = (
            PragmadinarStrategyReservation.objects.filter(supplier=supplier, pk=pk)
            .select_related("hold_ledger", "wallet", "service")
            .first()
        )
        if not plan:
            return Response({"error": "Réservation introuvable."}, status=404)
        if plan.status == PragmadinarStrategyReservation.Status.RESERVED:
            if plan.hold_ledger_id:
                release_hold(plan.hold_ledger, reason="Réservation libérée par le fournisseur")
            plan.status = PragmadinarStrategyReservation.Status.RELEASED
            plan.save(update_fields=["status", "updated_at"])
        return Response(
            {"reservation": _serialize_reservation(plan), "wallet": wallet_payload(supplier)}
        )


class SupplierPragmadinarStrategyVisitView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        supplier_id = getattr(request.user, "supplier_id", None)
        if not supplier_id:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        token = str(request.data.get("visitKey") or "")[:64]
        if not token:
            return Response({"error": "Identifiant de visite manquant."}, status=400)
        supplier = Supplier.objects.select_for_update().get(pk=supplier_id)
        if token != supplier.strategy_tool_last_visit_token:
            Supplier.objects.filter(pk=supplier.pk).update(
                strategy_tool_visits=F("strategy_tool_visits") + 1,
                strategy_tool_last_visit_token=token,
            )
        supplier.refresh_from_db(fields=["strategy_tool_visits"])
        return Response({"strategyToolVisits": supplier.strategy_tool_visits})


class SupplierPragmadinarStrategyReminderView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        last_shown = supplier.strategy_reminder_shown_at
        should_show = supplier.strategy_tool_visits < 3 and (
            last_shown is None or timezone.now() - last_shown >= REMINDER_INTERVAL
        )
        return Response(
            {"shouldShow": should_show, "strategyToolVisits": supplier.strategy_tool_visits}
        )

    def post(self, request):
        supplier = _supplier_for(request)
        if not supplier:
            return Response({"error": "Un compte fournisseur est requis."}, status=403)
        Supplier.objects.filter(pk=supplier.pk).update(strategy_reminder_shown_at=timezone.now())
        return Response({"ok": True})
