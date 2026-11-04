"""Ultra-admin supplier accounts and Pragmadinar ledger."""

from decimal import Decimal

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import rbac
from user.models import PragmadinarLedger, PragmadinarWallet, Supplier, User
from user.pragmadinar import (
    UNBILLED_SERVICE_CODES,
    WELCOME_GRANT_IDEMPOTENCY_PREFIX,
    PREVIOUS_WELCOME_GRANT_PREFIX,
    LEGACY_WELCOME_GRANT_PREFIX,
    adjust_supplier_balance,
    format_pragmadinar_amount,
)
from user.scoring import handle_supplier_created


def _deny_unless_ultra(request):
    user = request.user
    if not (user.is_authenticated and user.is_active and user.is_staff and rbac.is_ultra_admin(user)):
        return Response(
            {
                "error": "Réservé à l'ultra-administrateur.",
                "code": "ultra_admin_required",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _username_for(supplier):
    user = supplier.users.order_by("id").first()
    return user.username if user else ""


def _supplier_totals(supplier):
    wallets = PragmadinarWallet.objects.filter(supplier=supplier)
    balance = wallets.aggregate(total=Sum("balance"))["total"] or Decimal("0")
    entries = PragmadinarLedger.objects.filter(wallet__supplier=supplier, status=PragmadinarLedger.Status.POSTED)
    consumed = entries.filter(entry_type=PragmadinarLedger.EntryType.SPEND).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")
    bonuses = entries.filter(entry_type=PragmadinarLedger.EntryType.GRANT).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")
    added = entries.filter(
        entry_type=PragmadinarLedger.EntryType.ADJUSTMENT, amount__gt=0
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    last = (
        entries.filter(entry_type=PragmadinarLedger.EntryType.SPEND)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    count = entries.filter(entry_type=PragmadinarLedger.EntryType.SPEND).count()
    return {
        "balance": balance,
        "consumed": abs(consumed),
        "bonuses": bonuses,
        "adminAdded": added,
        "lastConsumption": last,
        "consumptionCount": count,
    }


def _entry_source(entry):
    key = entry.idempotency_key or ""
    if entry.entry_type == PragmadinarLedger.EntryType.GRANT and (
        key.startswith(WELCOME_GRANT_IDEMPOTENCY_PREFIX)
        or key.startswith(PREVIOUS_WELCOME_GRANT_PREFIX)
        or key.startswith(LEGACY_WELCOME_GRANT_PREFIX)
        or "bienvenue" in (entry.reason or "").lower()
    ):
        return "registration_bonus"
    if entry.entry_type == PragmadinarLedger.EntryType.ADJUSTMENT:
        return "administrative_adjustment"
    if entry.entry_type == PragmadinarLedger.EntryType.SPEND:
        if entry.service_id and entry.service and entry.service.code not in UNBILLED_SERVICE_CODES:
            return "service_consumption"
        return "legacy_unattributed"
    if entry.entry_type == PragmadinarLedger.EntryType.PURCHASE:
        return "purchase"
    if entry.entry_type == PragmadinarLedger.EntryType.REFUND:
        return "refund"
    if entry.entry_type == PragmadinarLedger.EntryType.TRANSFER:
        return "transfer"
    if entry.entry_type in {PragmadinarLedger.EntryType.HOLD, PragmadinarLedger.EntryType.RELEASE}:
        return "reservation"
    return "legacy_unattributed"


def _serialize_entry(entry):
    supplier = entry.wallet.supplier
    service = entry.service
    attributed = bool(service and service.code not in UNBILLED_SERVICE_CODES and entry.entry_type == "spend")
    return {
        "id": entry.pk,
        "supplierId": supplier.pk,
        "supplierName": supplier.company_name,
        "username": _username_for(supplier),
        "entryType": entry.entry_type,
        "source": _entry_source(entry),
        "amount": format_pragmadinar_amount(entry.amount),
        "balanceBefore": format_pragmadinar_amount(entry.balance_before) if entry.balance_before is not None else None,
        "balanceAfter": format_pragmadinar_amount(entry.balance_after) if entry.balance_after is not None else None,
        "createdAt": entry.created_at.isoformat(),
        "serviceCode": service.code if service else "",
        "serviceLabel": service.label if service and attributed else (
            service.label if service else "Historique non attribué"
        ),
        "attributed": attributed or entry.entry_type != "spend",
        "refType": entry.ref_type,
        "refId": entry.ref_id,
        "reason": entry.reason,
        "administratorId": entry.created_by_id,
        "reference": entry.reference,
    }


class AdminSupplierCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        confirm = request.data.get("passwordConfirm") or ""
        company = (request.data.get("companyName") or username).strip()
        if not username or not company:
            return Response({"error": "Le nom d'utilisateur est obligatoire."}, status=400)
        if password != confirm:
            return Response({"error": "Les mots de passe ne correspondent pas.", "code": "password_mismatch"}, status=400)
        if User.objects.filter(username=username).exists():
            return Response({"error": "Ce nom d'utilisateur existe déjà.", "code": "username_taken"}, status=400)
        email = f"{username}@suppliers.paknomik.local"
        if User.objects.filter(email=email).exists() or Supplier.objects.filter(email=email).exists():
            return Response({"error": "Ce nom d'utilisateur existe déjà.", "code": "username_taken"}, status=400)
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            return Response({"error": " ".join(exc.messages), "code": "invalid_password"}, status=400)

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username, email=email, password=password, company_name=company
                )
                supplier = Supplier.objects.create(company_name=company, email=email)
                user.supplier = supplier
                user.save(update_fields=["supplier"])
                handle_supplier_created(supplier, actor_user=request.user)
        except Exception:
            return Response({"error": "Le compte fournisseur n'a pas pu être créé."}, status=400)

        totals = _supplier_totals(supplier)
        return Response(
            {
                "supplierId": supplier.pk,
                "username": username,
                "companyName": supplier.company_name,
                "balance": format_pragmadinar_amount(totals["balance"]),
            },
            status=201,
        )


class AdminSupplierBalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        supplier = Supplier.objects.filter(pk=pk).first()
        if supplier is None:
            return Response({"error": "Fournisseur introuvable."}, status=404)
        if not request.data.get("confirm"):
            return Response({"error": "Confirmation obligatoire.", "code": "confirmation_required"}, status=400)
        try:
            entry = adjust_supplier_balance(
                supplier,
                mode=(request.data.get("mode") or "").strip(),
                amount=request.data.get("amount"),
                reason=request.data.get("reason") or "",
                actor=request.user,
            )
        except ValidationError as exc:
            detail = exc.detail
            message = detail.get("error") if isinstance(detail, dict) else "Ajustement refusé."
            return Response(
                {"error": message, "code": detail.get("code") if isinstance(detail, dict) else "invalid"},
                status=400,
            )
        rbac.log_audit(
            request.user,
            "pragmadinar.adjust",
            entity_type="Supplier",
            entity_id=supplier.pk,
            old_value={"balance": str(entry.balance_before)},
            new_value={"balance": str(entry.balance_after), "amount": str(entry.amount)},
            reason=entry.reason,
            request=request,
        )
        return Response(_serialize_entry(entry))


class AdminPragmadinarSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        posted = PragmadinarLedger.objects.filter(status=PragmadinarLedger.Status.POSTED)
        held = PragmadinarWallet.objects.aggregate(total=Sum("balance"))["total"] or Decimal("0")
        welcome_prefixes = (
            WELCOME_GRANT_IDEMPOTENCY_PREFIX,
            PREVIOUS_WELCOME_GRANT_PREFIX,
            LEGACY_WELCOME_GRANT_PREFIX,
        )
        welcome_q = Q()
        for prefix in welcome_prefixes:
            welcome_q |= Q(idempotency_key__startswith=prefix)
        granted = posted.filter(entry_type="grant").filter(welcome_q).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        admin_added = posted.filter(entry_type="adjustment", amount__gt=0).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        admin_removed = posted.filter(entry_type="adjustment", amount__lt=0).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        consumed = posted.filter(entry_type="spend").exclude(
            service__code__in=UNBILLED_SERVICE_CODES
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        positive = 0
        zero = 0
        for supplier_id in Supplier.objects.values_list("pk", flat=True):
            total = PragmadinarWallet.objects.filter(supplier_id=supplier_id).aggregate(
                total=Sum("balance")
            )["total"] or Decimal("0")
            if total > 0:
                positive += 1
            elif total == 0:
                zero += 1
        return Response(
            {
                "heldBySuppliers": format_pragmadinar_amount(held),
                "registrationBonuses": format_pragmadinar_amount(granted),
                "adminAdditions": format_pragmadinar_amount(admin_added),
                "serviceConsumption": format_pragmadinar_amount(abs(consumed)),
                "adminDeductions": format_pragmadinar_amount(abs(admin_removed)),
                "suppliersWithBalance": positive,
                "suppliersAtZero": zero,
            }
        )


class AdminPragmadinarSuppliersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        q = (request.query_params.get("q") or "").strip()
        try:
            page = max(int(request.query_params.get("page") or 1), 1)
        except ValueError:
            page = 1
        qs = Supplier.objects.all().order_by("company_name")
        if q:
            qs = qs.filter(
                Q(company_name__icontains=q) | Q(users__username__icontains=q)
            ).distinct()
        page_size = 20
        total = qs.count()
        rows = []
        for supplier in qs[(page - 1) * page_size : page * page_size]:
            totals = _supplier_totals(supplier)
            rows.append(
                {
                    "supplierId": supplier.pk,
                    "username": _username_for(supplier),
                    "companyName": supplier.company_name,
                    "balance": format_pragmadinar_amount(totals["balance"]),
                    "consumed": format_pragmadinar_amount(totals["consumed"]),
                    "bonuses": format_pragmadinar_amount(totals["bonuses"]),
                    "adminAdded": format_pragmadinar_amount(totals["adminAdded"]),
                    "lastConsumption": totals["lastConsumption"].isoformat() if totals["lastConsumption"] else None,
                    "consumptionCount": totals["consumptionCount"],
                }
            )
        return Response({"count": total, "page": page, "pageSize": page_size, "results": rows})


def _ledger_queryset(params):
    qs = PragmadinarLedger.objects.select_related(
        "wallet__supplier", "service", "created_by"
    ).order_by("-created_at", "-id")
    supplier_id = params.get("supplierId")
    if supplier_id:
        qs = qs.filter(wallet__supplier_id=supplier_id)
    entry_type = (params.get("entryType") or "").strip()
    if entry_type:
        qs = qs.filter(entry_type=entry_type)
    service = (params.get("service") or "").strip()
    if service:
        qs = qs.filter(service__code=service)
    direction = (params.get("direction") or "").strip()
    if direction == "positive":
        qs = qs.filter(amount__gt=0)
    elif direction == "negative":
        qs = qs.filter(amount__lt=0)
    start = parse_date(params.get("from") or "")
    end = parse_date(params.get("to") or "")
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)
    return qs


class AdminPragmadinarLedgerView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        try:
            page = max(int(request.query_params.get("page") or 1), 1)
        except ValueError:
            page = 1
        qs = _ledger_queryset(request.query_params)
        page_size = 25
        total = qs.count()
        rows = [_serialize_entry(entry) for entry in qs[(page - 1) * page_size : page * page_size]]
        return Response({"count": total, "page": page, "pageSize": page_size, "results": rows})


class AdminPragmadinarLedgerDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        denied = _deny_unless_ultra(request)
        if denied:
            return denied
        entry = (
            PragmadinarLedger.objects.select_related("wallet__supplier", "service", "created_by")
            .filter(pk=pk)
            .first()
        )
        if entry is None:
            return Response({"error": "Écriture introuvable."}, status=404)
        return Response(_serialize_entry(entry))
