import hashlib
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from user import rbac
from user.api.rbac_views import PermissionScopedView
from user.contact_models import ContactMessage
from user.social_links import (
    SOCIAL_SETTING_KEYS,
    SocialLinkError,
    current_social_links,
    normalize_social_url,
    save_social_links,
)

MAX_MESSAGES_PER_HOUR = 5
_PHONE_ALLOWED = re.compile(r"^\+?[\d\s().-]+$")


def _client_hash(request) -> str:
    ip = request.META.get("REMOTE_ADDR", "")
    return hashlib.sha256(f"{settings.SECRET_KEY}:{ip}".encode()).hexdigest()


def _optional_user(request):
    try:
        result = JWTAuthentication().authenticate(request)
    except Exception:
        return None
    return result[0] if result else None


def _parse_contact(raw: str):
    value = raw.strip()
    if "@" in value:
        try:
            validate_email(value)
        except ValidationError:
            return None, None
        return value.lower(), ContactMessage.ContactType.EMAIL
    digits = re.sub(r"\D", "", value)
    if _PHONE_ALLOWED.match(value) and 8 <= len(digits) <= 15:
        return value, ContactMessage.ContactType.PHONE
    return None, None


class PublicContactView(APIView):
    """Public "Contactez-nous" form. Anonymous visitors are allowed; an invalid or
    expired JWT must not block the submission, hence the manual optional auth."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data
        if (data.get("website") or "").strip():
            return Response({"ok": True}, status=status.HTTP_201_CREATED)

        errors = {}
        contact, contact_type = _parse_contact(str(data.get("contact") or ""))
        if not contact:
            errors["contact"] = "Indiquez une adresse email ou un numéro de téléphone valide."
        subject = str(data.get("subject") or "").strip()
        if not 3 <= len(subject) <= 150:
            errors["subject"] = "L'objet doit contenir entre 3 et 150 caractères."
        message = str(data.get("message") or "").strip()
        if not 10 <= len(message) <= 3000:
            errors["message"] = "Le message doit contenir entre 10 et 3000 caractères."
        if errors:
            return Response(
                {"error": "invalid_contact_form", "fields": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        client_hash = _client_hash(request)
        recent = ContactMessage.objects.filter(
            client_hash=client_hash,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent >= MAX_MESSAGES_PER_HOUR:
            return Response(
                {
                    "error": "contact_rate_limited",
                    "message": "Trop de messages envoyés. Réessayez dans une heure.",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        ContactMessage.objects.create(
            contact=contact,
            contact_type=contact_type,
            subject=subject,
            message=message,
            user=_optional_user(request),
            source=str(data.get("source") or "home")[:40],
            client_hash=client_hash,
        )
        return Response({"ok": True}, status=status.HTTP_201_CREATED)


class PublicSocialLinksView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        links = [
            {"platform": platform, "url": url}
            for platform, url in current_social_links().items()
            if url
        ]
        return Response({"links": links})


class AdminSocialLinksView(PermissionScopedView):
    required_permission = rbac.SETTINGS_MANAGE

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        return Response({"links": current_social_links()})

    def put(self, request):
        denied = self.check(request)
        if denied:
            return denied
        values, errors = {}, {}
        for platform in SOCIAL_SETTING_KEYS:
            if platform not in request.data:
                continue
            try:
                values[platform] = normalize_social_url(platform, str(request.data.get(platform) or ""))
            except SocialLinkError as exc:
                errors[platform] = str(exc)
        if errors:
            return Response(
                {"error": "invalid_social_links", "fields": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old = current_social_links()
        links = save_social_links(values)
        rbac.log_audit(
            request.user,
            "settings.social_links.update",
            entity_type="PlatformSetting",
            old_value=old,
            new_value=links,
            request=request,
        )
        return Response({"links": links})


def _contact_payload(msg):
    user = msg.user
    return {
        "id": msg.pk,
        "contact": msg.contact,
        "contactType": msg.contact_type,
        "subject": msg.subject,
        "message": msg.message,
        "status": msg.status,
        "source": msg.source,
        "createdAt": msg.created_at,
        "user": (
            {
                "id": user.pk,
                "email": user.email,
                "name": user.get_full_name() or user.username,
                "company": getattr(user, "company_name", "") or "",
            }
            if user
            else None
        ),
    }


class AdminContactMessagesView(PermissionScopedView):
    required_permission = rbac.SUPPORT_MANAGE
    MAX_ROWS = 200

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        from django.db.models import Count, Q

        qs = ContactMessage.objects.select_related("user")
        status_filter = (request.query_params.get("status") or "new").strip().lower()
        if status_filter in ContactMessage.Status.values:
            qs = qs.filter(status=status_filter)
        search = (request.query_params.get("q") or "").strip()
        if search:
            qs = qs.filter(
                Q(subject__icontains=search)
                | Q(contact__icontains=search)
                | Q(message__icontains=search)
            )
        counts = ContactMessage.objects.aggregate(
            new=Count("id", filter=Q(status=ContactMessage.Status.NEW)),
            handled=Count("id", filter=Q(status=ContactMessage.Status.HANDLED)),
        )
        return Response(
            {
                "messages": [_contact_payload(m) for m in qs[: self.MAX_ROWS]],
                "counts": counts,
                "truncated": qs.count() > self.MAX_ROWS,
            }
        )


class AdminContactMessageDetailView(PermissionScopedView):
    required_permission = rbac.SUPPORT_MANAGE

    def patch(self, request, pk):
        denied = self.check(request)
        if denied:
            return denied
        msg = ContactMessage.objects.select_related("user").filter(pk=pk).first()
        if msg is None:
            return Response({"error": "Message introuvable."}, status=status.HTTP_404_NOT_FOUND)
        new_status = (request.data.get("status") or "").strip().lower()
        if new_status not in ContactMessage.Status.values:
            return Response(
                {"error": "Statut invalide.", "code": "invalid_status"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_status = msg.status
        if new_status != old_status:
            msg.status = new_status
            msg.save(update_fields=["status"])
            rbac.log_audit(
                request.user,
                "contact_message.status",
                entity_type="ContactMessage",
                entity_id=msg.pk,
                old_value={"status": old_status},
                new_value={"status": new_status},
                request=request,
            )
        return Response(_contact_payload(msg))
