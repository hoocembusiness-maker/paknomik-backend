"""
JSON API for staff admin: roles, permissions, platform settings, audit log.

Only for Paknomik administrators. Used by the admin React app.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user import rbac
from user.models import (
    AuditLog,
    PragmadinarLedger,
    PragmadinarService,
    PlanEntitlement,
    PlatformRole,
    PlatformSetting,
    RoleGrant,
)
from user.pragmadinar import credit_wallet, refund_entry
from user.platform_config import DEFAULT_SETTINGS, pragmadinar_rate_dzd, set_setting

User = get_user_model()

#: Setting keys that must never be exposed or edited through the API.
SECRET_SETTING_PREFIXES = ("secret_", "token_", "api_key", "password", "private_")


def _permission_denied(exc):
    detail = exc.detail if hasattr(exc, "detail") else {"error": str(exc)}
    return Response(detail, status=status.HTTP_403_FORBIDDEN)


class PermissionScopedView(APIView):
    permission_classes = [IsAuthenticated]
    required_permission: str | None = None

    def check(self, request, permission=None):
        try:
            rbac.require_permission(request.user, permission or self.required_permission)
        except Exception as exc:  # DRF PermissionDenied
            return _permission_denied(exc)
        return None


class MyPermissionsView(APIView):
    """What the current account may do. Drives the admin UI, never authorizes."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "roles": sorted(rbac.active_role_codes(request.user)),
                "permissions": sorted(rbac.user_permissions(request.user)),
                "isUltraAdmin": rbac.is_ultra_admin(request.user),
            }
        )


class AdminRoleListView(PermissionScopedView):
    required_permission = rbac.ROLES_MANAGE

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        return Response(
            {
                "roles": [
                    {
                        "code": role.code,
                        "label": role.label,
                        "permissions": role.permissions,
                        "isSystem": role.is_system,
                        "isProtected": role.code in rbac.PROTECTED_ROLES,
                        "activeGrants": role.grants.filter(
                            revoked_at__isnull=True
                        ).count(),
                    }
                    for role in PlatformRole.objects.all()
                ],
                "permissions": rbac.ALL_PERMISSIONS,
            }
        )


class AdminRoleGrantView(PermissionScopedView):
    required_permission = rbac.ROLES_MANAGE

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        grants = RoleGrant.objects.filter(revoked_at__isnull=True).select_related(
            "user", "role", "granted_by"
        )
        return Response(
            {
                "results": [
                    {
                        "id": g.id,
                        "userId": g.user_id,
                        "userEmail": g.user.email,
                        "role": g.role.code,
                        "roleLabel": g.role.label,
                        "grantedBy": g.granted_by.email if g.granted_by_id else "",
                        "reason": g.reason,
                        "createdAt": g.created_at,
                    }
                    for g in grants[:500]
                ]
            }
        )

    def post(self, request):
        denied = self.check(request)
        if denied:
            return denied

        target = User.objects.filter(pk=request.data.get("userId")).first()
        role_code = (request.data.get("role") or "").strip()
        if target is None or not role_code:
            return Response(
                {"error": "Utilisateur ou rôle manquant."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target.pk == request.user.pk:
            # No self-escalation, whatever the role.
            return Response(
                {
                    "error": "Un utilisateur ne peut pas modifier ses propres rôles.",
                    "code": "self_grant_forbidden",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if not PlatformRole.objects.filter(code=role_code).exists():
            return Response(
                {"error": "Rôle inconnu."}, status=status.HTTP_400_BAD_REQUEST
            )

        reason = (request.data.get("reason") or "").strip()
        try:
            with transaction.atomic():
                grant = rbac.grant_role(
                    target, role_code, granted_by=request.user, reason=reason
                )
                rbac.log_audit(
                    request.user,
                    "roles.grant",
                    entity_type="User",
                    entity_id=target.pk,
                    new_value={"role": role_code},
                    reason=reason,
                    request=request,
                )
        except Exception as exc:
            return _permission_denied(exc)
        return Response({"granted": True, "grantId": grant.id})

    def delete(self, request):
        denied = self.check(request)
        if denied:
            return denied
        target = User.objects.filter(pk=request.data.get("userId")).first()
        role_code = (request.data.get("role") or "").strip()
        if target is None or not role_code:
            return Response(
                {"error": "Utilisateur ou rôle manquant."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if role_code in rbac.PROTECTED_ROLES and not rbac.is_ultra_admin(request.user):
            return Response(
                {"error": "Rôle protégé.", "code": "protected_role"},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Revoking keeps the historical grant rows and all produced data.
        count = rbac.revoke_role(target, role_code, revoked_by=request.user)
        rbac.log_audit(
            request.user,
            "roles.revoke",
            entity_type="User",
            entity_id=target.pk,
            old_value={"role": role_code},
            reason=(request.data.get("reason") or "").strip(),
            request=request,
        )
        return Response({"revoked": count})


class AdminAuditLogView(PermissionScopedView):
    required_permission = rbac.AUDIT_READ

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        qs = AuditLog.objects.select_related("actor")
        action = (request.query_params.get("action") or "").strip()
        if action:
            qs = qs.filter(action__startswith=action)
        entity = (request.query_params.get("entityType") or "").strip()
        if entity:
            qs = qs.filter(entity_type=entity)
        return Response(
            {
                "results": [
                    {
                        "id": row.id,
                        "actorId": row.actor_id,
                        "actorEmail": row.actor.email if row.actor_id else "système",
                        "action": row.action,
                        "entityType": row.entity_type,
                        "entityId": row.entity_id,
                        "oldValue": row.old_value,
                        "newValue": row.new_value,
                        "reason": row.reason,
                        "ip": row.ip_address,
                        "createdAt": row.created_at,
                    }
                    for row in qs[:300]
                ]
            }
        )


class AdminPricingView(PermissionScopedView):
    """Tariffs and plan limits, editable without a frontend deploy."""

    required_permission = rbac.PRAGMADINARS_PRICING_MANAGE

    def check(self, request, permission=None):
        try:
            rbac.require_pricing_manage(request.user)
        except Exception as exc:
            return _permission_denied(exc)
        return None

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        return Response(
            {
                "pragmadinarRateDzd": str(pragmadinar_rate_dzd()),
                "services": [
                    {
                        "code": s.code,
                        "label": s.label,
                        "serviceClass": s.service_class,
                        "cost": str(s.cost_pragmadinar),
                        "unit": s.unit,
                        "unitQuantity": s.unit_quantity,
                        "unitLabel": s.unit_label,
                        "priceRank": s.price_rank,
                        "businessAccess": s.business_access,
                        "genereuxAccess": s.genereux_access,
                        "isActive": s.is_active,
                    }
                    for s in PragmadinarService.objects.all()
                ],
                "entitlements": [
                    {
                        "plan": e.plan_code,
                        "key": e.key,
                        "label": e.label,
                        "limit": e.limit_value,
                        "unlimited": e.limit_value is None,
                        "isEnabled": e.is_enabled,
                    }
                    for e in PlanEntitlement.objects.all()
                ],
            }
        )

    def patch(self, request):
        denied = self.check(request)
        if denied:
            return denied

        updated: list[str] = []
        if "pragmadinarRateDzd" in request.data:
            raw = request.data.get("pragmadinarRateDzd")
            old = PlatformSetting.objects.filter(key="pragmadinar_rate_dzd").first()
            set_setting("pragmadinar_rate_dzd", raw)
            rbac.log_audit(
                request.user,
                "settings.update",
                entity_type="PlatformSetting",
                entity_id=old.pk if old else None,
                old_value={"value": old.value} if old else {},
                new_value={"value": str(raw)},
                reason=(request.data.get("reason") or "Tarification admin")[:255],
                request=request,
            )
            updated.append("pragmadinarRateDzd")

        code = (request.data.get("code") or "").strip()
        if not code:
            if updated:
                return Response({"updated": updated})
            return Response(
                {"error": "Indiquez le code service ou pragmadinarRateDzd."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        service = PragmadinarService.objects.filter(code=code).first()
        if service is None:
            return Response(
                {"error": "Service inconnu."}, status=status.HTTP_400_BAD_REQUEST
            )
        old = {
            "cost": str(service.cost_pragmadinar),
            "isActive": service.is_active,
            "businessAccess": service.business_access,
        }
        changed = []
        if "cost" in request.data:
            service.cost_pragmadinar = request.data.get("cost") or 0
            changed.append("cost_pragmadinar")
        if "isActive" in request.data:
            service.is_active = bool(request.data.get("isActive"))
            changed.append("is_active")
        if "businessAccess" in request.data:
            service.business_access = request.data.get("businessAccess")
            changed.append("business_access")
        if "genereuxAccess" in request.data:
            service.genereux_access = request.data.get("genereuxAccess")
            changed.append("genereux_access")
        if "unitLabel" in request.data:
            service.unit_label = str(request.data.get("unitLabel") or "").strip()[:80]
            changed.append("unit_label")
        if "priceRank" in request.data:
            raw_rank = request.data.get("priceRank")
            try:
                service.price_rank = int(raw_rank) if raw_rank not in (None, "") else None
            except (TypeError, ValueError):
                return Response(
                    {"error": "Rang tarifaire invalide."}, status=status.HTTP_400_BAD_REQUEST
                )
            changed.append("price_rank")
        if changed:
            service.save(update_fields=changed)
            rbac.log_audit(
                request.user,
                "pragmadinars.pricing.update",
                entity_type="PragmadinarService",
                entity_id=service.pk,
                old_value=old,
                new_value={
                    "cost": str(service.cost_pragmadinar),
                    "isActive": service.is_active,
                    "businessAccess": service.business_access,
                },
                reason=(request.data.get("reason") or "").strip(),
                request=request,
            )
            updated.extend(changed)
        return Response({"updated": updated})


class AdminEntitlementView(PermissionScopedView):
    required_permission = rbac.SUBSCRIPTIONS_MANAGE

    def patch(self, request):
        denied = self.check(request)
        if denied:
            return denied
        plan = (request.data.get("plan") or "").strip()
        key = (request.data.get("key") or "").strip()
        row = PlanEntitlement.objects.filter(plan_code=plan, key=key).first()
        if row is None:
            return Response(
                {"error": "Entitlement inconnu."}, status=status.HTTP_400_BAD_REQUEST
            )
        old = {"limit": row.limit_value, "isEnabled": row.is_enabled}
        if "limit" in request.data:
            raw = request.data.get("limit")
            row.limit_value = None if raw in (None, "", "unlimited") else int(raw)
        if "isEnabled" in request.data:
            row.is_enabled = bool(request.data.get("isEnabled"))
        row.save(update_fields=["limit_value", "is_enabled", "updated_at"])
        rbac.log_audit(
            request.user,
            "subscriptions.entitlement.update",
            entity_type="PlanEntitlement",
            entity_id=row.pk,
            old_value=old,
            new_value={"limit": row.limit_value, "isEnabled": row.is_enabled},
            request=request,
        )
        return Response({"updated": True})


class AdminSettingsView(PermissionScopedView):
    required_permission = rbac.SETTINGS_MANAGE

    def get(self, request):
        denied = self.check(request)
        if denied:
            return denied
        visible = [
            r
            for r in PlatformSetting.objects.all()
            if not any(r.key.startswith(p) for p in SECRET_SETTING_PREFIXES)
        ]
        return Response(
            {
                "settings": [
                    {"key": r.key, "value": r.value, "label": r.label}
                    for r in visible
                ],
                "known": [
                    {"key": k, "default": v[0], "label": v[1]}
                    for k, v in DEFAULT_SETTINGS.items()
                ],
            }
        )

    def patch(self, request):
        denied = self.check(request)
        if denied:
            return denied
        key = (request.data.get("key") or "").strip()
        if not key or any(key.startswith(p) for p in SECRET_SETTING_PREFIXES):
            return Response(
                {
                    "error": "Cette clé ne peut pas être modifiée ici.",
                    "code": "protected_setting",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        old = PlatformSetting.objects.filter(key=key).first()
        set_setting(key, request.data.get("value"))
        rbac.log_audit(
            request.user,
            "settings.update",
            entity_type="PlatformSetting",
            entity_id=old.pk if old else None,
            old_value={"value": old.value} if old else {},
            new_value={"value": str(request.data.get("value"))},
            reason=(request.data.get("reason") or "").strip(),
            request=request,
        )
        return Response({"updated": True})


class AdminPragmadinarCreditView(PermissionScopedView):
    required_permission = rbac.PRAGMADINARS_CREDIT

    def post(self, request):
        denied = self.check(request)
        if denied:
            return denied
        from user.models import PragmadinarLedger as Ledger, Supplier

        supplier = Supplier.objects.filter(pk=request.data.get("supplierId")).first()
        if supplier is None:
            return Response(
                {"error": "Fournisseur introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        amount = request.data.get("amount")
        is_grant = bool(request.data.get("isGrant"))
        entry = credit_wallet(
            supplier,
            amount,
            reason=(request.data.get("reason") or "Crédit administrateur")[:255],
            entry_type=Ledger.EntryType.GRANT if is_grant else Ledger.EntryType.PURCHASE,
            idempotency_key=(request.data.get("idempotencyKey") or "").strip(),
            actor=request.user,
        )
        rbac.log_audit(
            request.user,
            "pragmadinars.credit",
            entity_type="Supplier",
            entity_id=supplier.pk,
            new_value={"amount": str(amount), "reference": entry.reference},
            reason=(request.data.get("reason") or "").strip(),
            request=request,
        )
        return Response({"reference": entry.reference, "amount": str(entry.amount)})


class AdminPragmadinarRefundView(PermissionScopedView):
    required_permission = rbac.PRAGMADINARS_REFUND

    def post(self, request):
        denied = self.check(request)
        if denied:
            return denied
        entry = PragmadinarLedger.objects.filter(pk=request.data.get("entryId")).first()
        if entry is None:
            return Response(
                {"error": "Transaction introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response(
                {
                    "error": "Un motif est obligatoire pour un remboursement.",
                    "code": "reason_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        refund = refund_entry(entry, actor=request.user, reason=reason)
        rbac.log_audit(
            request.user,
            "pragmadinars.refund",
            entity_type="PragmadinarLedger",
            entity_id=entry.pk,
            old_value={"amount": str(entry.amount)},
            new_value={"reference": refund.reference},
            reason=reason,
            request=request,
        )
        return Response({"reference": refund.reference})
