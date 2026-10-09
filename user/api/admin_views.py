"""
JSON API for Paknomik staff: support, users, platform tools.
"""

import os
import secrets

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import rbac
from user.models import Event, QuoteDemand, Supplier, SupportConversation, SupportMessage
from user.quote_market import PUBLIC_MARKET_DAYS, expire_stale_public_market_listings
from user.events import log_buyer_event, log_seller_event
from user.platform_intel import build_platform_intel_dashboard
from user.platform_usage import build_platform_usage_dashboard
from user.platform_commercial import build_platform_commercial_dashboard
from user.reporting_period import resolve_report_period
from user.api.serializers import (
    EventSerializer,
    SupplierMessageCreateSerializer,
    SupplierProfileEditSerializer,
    SupplierSettingsSerializer,
    SupportMessageSerializer,
    contact_display_name,
    ensure_supplier_settings,
    user_avatar_url,
)
from user.api.views import _auth_payload, _buyer_display_name

User = get_user_model()


def _require_staff(user):
    """Minimum bar for any admin route. Never sufficient on its own."""
    return user.is_authenticated and user.is_active and user.is_staff


def _staff_forbidden():
    return Response(
        {"error": "Staff access required"},
        status=status.HTTP_403_FORBIDDEN,
    )


def _deny(request, *permissions):
    """Authorize a route server-side. Returns a Response when refused.

    An account must be staff **and** hold every listed atomic permission, so a
    support admin can never reach a finance route by guessing the URL.
    """
    if not _require_staff(request.user):
        return _staff_forbidden()
    for permission in permissions:
        if not rbac.has_permission(request.user, permission):
            return Response(
                {
                    "error": "Vous n'avez pas la permission requise pour cette action.",
                    "code": "permission_denied",
                    "requiredPermission": permission,
                },
                status=status.HTTP_403_FORBIDDEN,
            )
    return None


def _trace(request, action, *, entity_type="", entity_id=None, reason="", extra=None):
    """Record a sensitive consultation or change in the audit log."""
    return rbac.log_audit(
        request.user,
        action,
        entity_type=entity_type,
        entity_id=entity_id,
        new_value=extra or {},
        reason=reason,
        request=request,
    )


def _support_conversation_payload(request, conversation):
    last_message = (
        conversation.messages.order_by("-created_at").values_list("content", flat=True).first()
    )
    unread_count = conversation.messages.filter(
        is_staff_reply=False,
        read_at__isnull=True,
    ).count()
    user = conversation.user
    supplier = getattr(user, "supplier", None)
    supplier_name = (getattr(supplier, "company_name", None) or "").strip()
    return {
        "id": conversation.id,
        "userId": user.id,
        "userName": contact_display_name(user),
        "userEmail": user.email,
        "userCompany": user.company_name or supplier_name,
        "supplierName": supplier_name,
        "supplierId": getattr(user, "supplier_id", None),
        "userAvatar": user_avatar_url(request, user),
        "isSupplier": bool(user.supplier_id),
        "subject": conversation.subject,
        "status": conversation.status,
        "lastMessage": (last_message or "")[:160],
        "lastMessageAt": conversation.updated_at,
        "unreadCount": unread_count,
        "createdAt": conversation.created_at,
    }


def _mark_support_read_by_staff(conversation):
    SupportMessage.objects.filter(
        conversation=conversation,
        is_staff_reply=False,
        read_at__isnull=True,
    ).update(read_at=timezone.now())


def _mark_support_read_by_user(conversation):
    SupportMessage.objects.filter(
        conversation=conversation,
        is_staff_reply=True,
        read_at__isnull=True,
    ).update(read_at=timezone.now())


class AdminEventsMetaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_staff(request.user):
            return _staff_forbidden()

        categories = {"buyer": [], "seller": [], "system": []}
        for value, label in Event.EventType.choices:
            prefix = value.split(".", 1)[0]
            if prefix in categories:
                categories[prefix].append({"value": value, "label": label})

        counts = {
            "total": Event.objects.count(),
            "buyer": Event.objects.filter(event_type__startswith="buyer.").count(),
            "seller": Event.objects.filter(event_type__startswith="seller.").count(),
            "system": Event.objects.filter(event_type__startswith="system.").count(),
        }

        return Response({"categories": categories, "counts": counts})


class AdminEventsListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_staff(request.user):
            return _staff_forbidden()

        category = request.query_params.get("category", "").strip().lower()
        event_type = request.query_params.get("eventType", "").strip()
        search = request.query_params.get("q", "").strip()
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(100, max(1, int(request.query_params.get("pageSize", 50))))
        except (TypeError, ValueError):
            page_size = 50

        qs = Event.objects.select_related(
            "actor_user",
            "actor_user__supplier",
        ).order_by("-created_at")

        if category in {"buyer", "seller", "system"}:
            qs = qs.filter(event_type__startswith=f"{category}.")
        if event_type:
            qs = qs.filter(event_type=event_type)
        if search:
            qs = qs.filter(
                Q(event_type__icontains=search)
                | Q(actor_user__email__icontains=search)
                | Q(actor_user__username__icontains=search)
            )

        total = qs.count()
        offset = (page - 1) * page_size
        events = qs[offset : offset + page_size]

        return Response(
            {
                "results": EventSerializer(events, many=True).data,
                "total": total,
                "page": page,
                "pageSize": page_size,
                "totalPages": max(1, (total + page_size - 1) // page_size),
            }
        )


class AdminSupportConversationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_staff(request.user):
            return _staff_forbidden()

        status_filter = request.query_params.get("status", "open").strip().lower()
        search = request.query_params.get("q", "").strip()
        qs = SupportConversation.objects.select_related("user", "user__supplier").order_by(
            "-updated_at"
        )
        if status_filter in {"open", "closed"}:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(
                Q(user__email__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(user__company_name__icontains=search)
                | Q(user__supplier__company_name__icontains=search)
                | Q(user__supplier__email__icontains=search)
                | Q(subject__icontains=search)
            )

        conversations = [_support_conversation_payload(request, conv) for conv in qs]
        return Response(conversations)


class AdminSupportConversationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        if not _require_staff(request.user):
            return _staff_forbidden()

        try:
            conversation = SupportConversation.objects.get(pk=pk)
        except SupportConversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get("status")
        if new_status in {SupportConversation.Status.OPEN, SupportConversation.Status.CLOSED}:
            conversation.status = new_status
            conversation.save(update_fields=["status", "updated_at"])

        return Response(_support_conversation_payload(request, conversation))


class AdminSupportMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not _require_staff(request.user):
            return _staff_forbidden()

        try:
            conversation = SupportConversation.objects.get(pk=pk)
        except SupportConversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        _mark_support_read_by_staff(conversation)
        qs = conversation.messages.select_related("sender").order_by("created_at")
        return Response(
            SupportMessageSerializer(qs, many=True, context={"request": request}).data
        )

    def post(self, request, pk):
        if not _require_staff(request.user):
            return _staff_forbidden()

        try:
            conversation = SupportConversation.objects.get(pk=pk)
        except SupportConversation.DoesNotExist:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = SupplierMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        message = SupportMessage.objects.create(
            conversation=conversation,
            sender=request.user,
            content=serializer.validated_data["content"],
            is_staff_reply=True,
        )
        conversation.status = SupportConversation.Status.OPEN
        conversation.save(update_fields=["status", "updated_at"])

        return Response(
            SupportMessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class UserSupportConversationView(APIView):
    """Get or create the user's open tech support conversation."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        conversation = (
            SupportConversation.objects.filter(
                user=request.user,
                status=SupportConversation.Status.OPEN,
            )
            .select_related("user")
            .order_by("-updated_at")
            .first()
        )
        if not conversation:
            conversation = SupportConversation.objects.create(user=request.user)
        return Response(_support_conversation_payload(request, conversation))

    def post(self, request):
        subject = (request.data.get("subject") or "Assistance technique").strip()[:255]
        conversation = SupportConversation.objects.create(
            user=request.user,
            subject=subject or "Assistance technique",
        )
        initial_message = (request.data.get("message") or "").strip()
        if initial_message:
            SupportMessage.objects.create(
                conversation=conversation,
                sender=request.user,
                content=initial_message,
                is_staff_reply=False,
            )
            conversation.save(update_fields=["updated_at"])
        return Response(
            _support_conversation_payload(request, conversation),
            status=status.HTTP_201_CREATED,
        )


class UserSupportMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_user_conversation(self, user, pk):
        try:
            return SupportConversation.objects.get(pk=pk, user=user)
        except SupportConversation.DoesNotExist:
            return None

    def get(self, request, pk):
        conversation = self._get_user_conversation(request.user, pk)
        if not conversation:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        _mark_support_read_by_user(conversation)
        qs = conversation.messages.select_related("sender").order_by("created_at")
        return Response(
            SupportMessageSerializer(qs, many=True, context={"request": request}).data
        )

    def post(self, request, pk):
        conversation = self._get_user_conversation(request.user, pk)
        if not conversation:
            return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        if conversation.status == SupportConversation.Status.CLOSED:
            conversation.status = SupportConversation.Status.OPEN
            conversation.save(update_fields=["status", "updated_at"])

        serializer = SupplierMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        message = SupportMessage.objects.create(
            conversation=conversation,
            sender=request.user,
            content=serializer.validated_data["content"],
            is_staff_reply=False,
        )
        conversation.save(update_fields=["updated_at"])

        return Response(
            SupportMessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


def _admin_platform_user_payload(request, user):
    supplier = getattr(user, "supplier", None)
    company = user.company_name or (supplier.company_name if supplier else "")
    return {
        "id": user.id,
        "email": user.email,
        "name": _buyer_display_name(user) or user.username,
        "company": company,
        "isSupplier": bool(user.supplier_id),
        "supplierId": user.supplier_id,
        "roleLabel": "Fournisseur" if user.supplier_id else "Acheteur",
        "avatar": user_avatar_url(request, user),
        "isActive": user.is_active,
    }


class AdminPlatformUsersView(APIView):
    """List buyer and seller accounts for staff impersonation."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_staff(request.user):
            return _staff_forbidden()

        role = request.query_params.get("role", "all").strip().lower()
        search = request.query_params.get("q", "").strip()

        qs = User.objects.filter(is_active=True, is_staff=False).select_related("supplier")
        if role == "buyer":
            qs = qs.filter(supplier_id__isnull=True)
        elif role == "seller":
            qs = qs.filter(supplier_id__isnull=False)
        if search:
            qs = qs.filter(
                Q(email__icontains=search)
                | Q(username__icontains=search)
                | Q(company_name__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(supplier__company_name__icontains=search)
            )

        users = qs.order_by("-date_joined")[:250]
        return Response([_admin_platform_user_payload(request, user) for user in users])


class AdminImpersonateView(APIView):
    """Issue a JWT for the target user (staff impersonation)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        if not _require_staff(request.user):
            return _staff_forbidden()

        try:
            target = User.objects.select_related("supplier").get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        if target.is_staff:
            return Response(
                {"error": "Cannot impersonate staff accounts"},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = _auth_payload(target)
        payload["isImpersonating"] = True

        if target.supplier_id:
            log_seller_event(
                target,
                Event.EventType.SELLER_LOGIN,
                {
                    "method": "admin_impersonation",
                    "staff_id": request.user.id,
                },
            )
        else:
            log_buyer_event(
                target,
                Event.EventType.BUYER_LOGIN,
                {
                    "method": "admin_impersonation",
                    "staff_id": request.user.id,
                },
            )

        return Response(payload)


class AdminIntelDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_staff(request.user):
            return _staff_forbidden()

        period = resolve_report_period(request.query_params)
        metric = request.query_params.get("metric", "visit").strip().lower()

        return Response(build_platform_intel_dashboard(period=period, metric=metric))


class AdminUsageDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_staff(request.user):
            return _staff_forbidden()

        period = resolve_report_period(request.query_params)
        role = request.query_params.get("role", "buyer").strip().lower()

        return Response(build_platform_usage_dashboard(period=period, role=role))


class AdminCommercialDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _require_staff(request.user):
            return _staff_forbidden()

        period = resolve_report_period(request.query_params)
        metric = request.query_params.get("metric", "interactions").strip().lower()

        return Response(build_platform_commercial_dashboard(period=period, metric=metric))


#: Fallback kept only so existing installs keep working; override with the
#: PAKNOMIK_SUPPLIER_SETTINGS_CODE environment variable. A secret must not live
#: in the source tree, and it is never returned by any endpoint.
SUPPLIER_SETTINGS_ACCESS_CODE_FALLBACK = "4655bomb"


def _supplier_settings_access_code() -> str:
    return (
        os.environ.get("PAKNOMIK_SUPPLIER_SETTINGS_CODE")
        or SUPPLIER_SETTINGS_ACCESS_CODE_FALLBACK
    ).strip()


def _supplier_settings_access_denied():
    return Response(
        {"error": "Code d'accès invalide."},
        status=status.HTTP_403_FORBIDDEN,
    )


def _verify_supplier_settings_access(request) -> bool:
    code = (
        request.headers.get("X-Supplier-Settings-Code")
        or request.data.get("accessCode")
        or ""
    ).strip()
    expected = _supplier_settings_access_code()
    # Constant-time comparison so the code cannot be guessed byte by byte.
    return bool(code) and secrets.compare_digest(code, expected)


def _require_supplier_settings_access(request):
    if not _require_staff(request.user):
        return _staff_forbidden()
    if not _verify_supplier_settings_access(request):
        return _supplier_settings_access_denied()
    return None


def _parse_id_list(raw):
    if raw is None:
        return None
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        try:
            import json

            parsed = json.loads(raw)
            values = parsed if isinstance(parsed, list) else [raw]
        except (json.JSONDecodeError, TypeError):
            values = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        values = [raw]
    return [int(value) for value in values if str(value).strip()]


class AdminSuppliersListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _require_supplier_settings_access(request)
        if denied:
            return denied

        search = request.query_params.get("q", "").strip()
        qs = Supplier.objects.all().order_by("company_name")
        if search:
            qs = qs.filter(
                Q(company_name__icontains=search)
                | Q(email__icontains=search)
                | Q(wilaya__icontains=search)
            )
        suppliers = qs[:300]
        return Response(
            [
                {
                    "id": supplier.id,
                    "companyName": supplier.company_name,
                    "email": supplier.email,
                    "wilaya": supplier.get_wilaya_display() if supplier.wilaya else "",
                    "subscriptionType": supplier.subscription_type or "Classic",
                    "isActive": supplier.is_active,
                    "isFeatured": supplier.is_featured,
                    "brandColor": supplier.brand_color or "",
                    "brandNeutralColor": supplier.brand_neutral_color or "#ffffff",
                }
                for supplier in suppliers
            ]
        )


class AdminSupplierManageView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_supplier(self, supplier_id):
        return Supplier.objects.filter(pk=supplier_id).first()

    def get(self, request, supplier_id):
        denied = _require_supplier_settings_access(request)
        if denied:
            return denied

        supplier = self._get_supplier(supplier_id)
        if not supplier:
            return Response({"error": "Supplier not found"}, status=status.HTTP_404_NOT_FOUND)

        settings_obj = ensure_supplier_settings(supplier)
        profile = SupplierProfileEditSerializer(
            supplier, context={"request": request}
        ).data
        profile["wilayaOptions"] = [
            {"value": value, "label": label}
            for value, label in Supplier.WILAYA_CHOICES
        ]
        profile["subscriptionType"] = supplier.subscription_type or "Classic"
        profile["isActive"] = supplier.is_active
        profile["isFeatured"] = supplier.is_featured
        return Response(
            {
                "profile": profile,
                "settings": SupplierSettingsSerializer(settings_obj).data,
            }
        )

    def patch(self, request, supplier_id):
        denied = _require_supplier_settings_access(request)
        if denied:
            return denied

        supplier = self._get_supplier(supplier_id)
        if not supplier:
            return Response({"error": "Supplier not found"}, status=status.HTTP_404_NOT_FOUND)

        profile_payload = request.data.get("profile")
        settings_payload = request.data.get("settings")

        if profile_payload is not None:
            payload = {}
            for api_key in (
                "companyName",
                "whatsappNumber",
                "wilaya",
                "address",
                "website",
                "presentation",
                "type",
                "brandColor",
                "brandNeutralColor",
            ):
                if api_key in profile_payload:
                    payload[api_key] = profile_payload.get(api_key) or ""

            serializer = SupplierProfileEditSerializer(
                supplier,
                data=payload,
                partial=True,
                context={
                    "request": request,
                    "category_ids": _parse_id_list(profile_payload.get("categoryIds")),
                    "subcategory_ids": _parse_id_list(
                        profile_payload.get("subcategoryIds")
                    ),
                    "logo_file": request.FILES.get("logo"),
                },
            )
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            supplier = serializer.save()

            admin_fields = []
            if "subscriptionType" in profile_payload:
                supplier.subscription_type = profile_payload.get("subscriptionType") or "Classic"
                admin_fields.append("subscription_type")
            if "isActive" in profile_payload:
                supplier.is_active = bool(profile_payload.get("isActive"))
                admin_fields.append("is_active")
            if "isFeatured" in profile_payload:
                supplier.is_featured = bool(profile_payload.get("isFeatured"))
                admin_fields.append("is_featured")
            if admin_fields:
                admin_fields.append("updated_at")
                supplier.save(update_fields=admin_fields)

        if settings_payload is not None:
            settings_obj = ensure_supplier_settings(supplier)
            settings_serializer = SupplierSettingsSerializer(
                settings_obj,
                data=settings_payload,
                partial=True,
            )
            if not settings_serializer.is_valid():
                return Response(
                    settings_serializer.errors, status=status.HTTP_400_BAD_REQUEST
                )
            settings_serializer.save()

        settings_obj = ensure_supplier_settings(supplier)
        profile = SupplierProfileEditSerializer(
            supplier, context={"request": request}
        ).data
        profile["wilayaOptions"] = [
            {"value": value, "label": label}
            for value, label in Supplier.WILAYA_CHOICES
        ]
        profile["subscriptionType"] = supplier.subscription_type or "Classic"
        profile["isActive"] = supplier.is_active
        profile["isFeatured"] = supplier.is_featured
        return Response(
            {
                "profile": profile,
                "settings": SupplierSettingsSerializer(settings_obj).data,
            }
        )


def _admin_quote_demand_row(request, demand):
    from user.api.quote_demand_views import _category_name

    buyer = demand.buyer
    buyer_name = (
        buyer.company_name or buyer.get_full_name() or buyer.username or ""
    )
    return {
        "id": demand.id,
        "kind": demand.kind,
        "status": demand.status,
        "categoryName": _category_name(demand.category),
        "subcategoryName": _category_name(demand.subcategory),
        "quantity": demand.quantity,
        "description": demand.description[:500],
        "wilaya": demand.wilaya,
        "createdAt": demand.created_at.isoformat(),
        "visibleOnMarket": demand.visible_on_market,
        "adminQueue": demand.admin_queue,
        "marketRemovedReason": demand.market_removed_reason or None,
        "engagedCount": demand.engagements.count(),
        "proposalCount": demand.proposals.count(),
        "buyerId": buyer.id,
        "buyerName": buyer_name,
        "buyerEmail": buyer.email,
        "marketDays": PUBLIC_MARKET_DAYS,
    }


class AdminQuoteDemandsQueueView(APIView):
    """Open public quote demands handed to Paknomik staff."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = _deny(request, rbac.QUOTES_READ)
        if denied:
            return denied
        expire_stale_public_market_listings()
        qs = (
            QuoteDemand.objects.filter(admin_queue=True)
            .exclude(status=QuoteDemand.Status.RESOLVED)
            .select_related("buyer", "category", "subcategory")
            .prefetch_related("engagements", "proposals")
            .order_by("-created_at")[:200]
        )
        _trace(request, "admin.quote_demands.queue_read")
        results = [_admin_quote_demand_row(request, d) for d in qs]
        return Response({"results": results, "count": len(results)})


class AdminQuoteDemandQueueItemView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        denied = _deny(request, rbac.QUOTES_MANAGE)
        if denied:
            return denied
        demand = (
            QuoteDemand.objects.filter(pk=pk)
            .select_related("buyer", "category", "subcategory")
            .prefetch_related("engagements", "proposals")
            .first()
        )
        if not demand:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        action = (request.data.get("action") or "ack").strip()
        if action == "ack":
            demand.admin_queue = False
            demand.save(update_fields=["admin_queue", "updated_at"])
            _trace(
                request,
                "admin.quote_demands.ack",
                entity_type="quote_demand",
                entity_id=demand.id,
            )
        elif action == "close":
            if demand.status == QuoteDemand.Status.RESOLVED:
                return Response(
                    {"error": "Demande déjà résolue."},
                    status=status.HTTP_409_CONFLICT,
                )
            demand.status = QuoteDemand.Status.CLOSED
            demand.visible_on_market = False
            demand.admin_queue = False
            demand.save(
                update_fields=[
                    "status",
                    "visible_on_market",
                    "admin_queue",
                    "updated_at",
                ]
            )
            _trace(
                request,
                "admin.quote_demands.close",
                entity_type="quote_demand",
                entity_id=demand.id,
            )
        else:
            return Response(
                {"error": "action invalide (ack ou close)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_admin_quote_demand_row(request, demand))
