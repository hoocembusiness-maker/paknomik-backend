"""
Who is allowed to do what on Paknomik (roles and permissions).

Staff users get roles such as support or ultra-admin. Each role has a list
of permissions. API views call helpers here to block unauthorized access.
Never rely only on hiding a button in the frontend — always check on the server.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

# ── Atomic permissions ──────────────────────────────────────────────────────

USERS_READ = "users.read"
USERS_UPDATE = "users.update"
USERS_SUSPEND = "users.suspend"
SUPPLIERS_READ = "suppliers.read"
SUPPLIERS_MANAGE = "suppliers.manage"
PRODUCTS_MANAGE = "products.manage"
CRM_READ = "crm.read"
CRM_READ_PRIVATE = "crm.read_private"
QUOTES_READ = "quotes.read"
QUOTES_MANAGE = "quotes.manage"
ORDERS_READ = "orders.read"
ORDERS_MANAGE = "orders.manage"
PRAGMADINARS_READ = "pragmadinars.read"
PRAGMADINARS_CREDIT = "pragmadinars.credit"
PRAGMADINARS_REFUND = "pragmadinars.refund"
PRAGMADINARS_PRICING_MANAGE = "pragmadinars.pricing.manage"
SUBSCRIPTIONS_READ = "subscriptions.read"
SUBSCRIPTIONS_MANAGE = "subscriptions.manage"
CAMPAIGNS_MANAGE = "campaigns.manage"
ANALYTICS_READ = "analytics.read"
ANALYTICS_PRIVATE = "analytics.private"
REPORTS_GENERATE = "reports.generate"
MODERATION_MANAGE = "moderation.manage"
SUPPORT_MANAGE = "support.manage"
ROLES_MANAGE = "roles.manage"
SETTINGS_MANAGE = "settings.manage"
AUDIT_READ = "audit.read"
CONSENT_MANAGE = "consent.manage"
COMMISSIONS_READ = "commissions.read"
COMMISSIONS_MANAGE = "commissions.manage"
PILOTAGE_ACCESS = "pilotage.access"
PILOTAGE_MANAGE = "pilotage.manage"

ALL_PERMISSIONS = [
    USERS_READ,
    USERS_UPDATE,
    USERS_SUSPEND,
    SUPPLIERS_READ,
    SUPPLIERS_MANAGE,
    PRODUCTS_MANAGE,
    CRM_READ,
    CRM_READ_PRIVATE,
    QUOTES_READ,
    QUOTES_MANAGE,
    ORDERS_READ,
    ORDERS_MANAGE,
    PRAGMADINARS_READ,
    PRAGMADINARS_CREDIT,
    PRAGMADINARS_REFUND,
    PRAGMADINARS_PRICING_MANAGE,
    SUBSCRIPTIONS_READ,
    SUBSCRIPTIONS_MANAGE,
    CAMPAIGNS_MANAGE,
    ANALYTICS_READ,
    ANALYTICS_PRIVATE,
    REPORTS_GENERATE,
    MODERATION_MANAGE,
    SUPPORT_MANAGE,
    ROLES_MANAGE,
    SETTINGS_MANAGE,
    AUDIT_READ,
    CONSENT_MANAGE,
    COMMISSIONS_READ,
    COMMISSIONS_MANAGE,
    PILOTAGE_ACCESS,
    PILOTAGE_MANAGE,
]

# ── Roles ───────────────────────────────────────────────────────────────────

ULTRA_ADMIN = "ULTRA_ADMIN"
ADMIN_GENERAL = "ADMIN_GENERAL"
ADMIN_OPERATIONS = "ADMIN_OPERATIONS"
ADMIN_SUPPORT = "ADMIN_SUPPORT"
ADMIN_FINANCE = "ADMIN_FINANCE"
ANALYST = "ANALYST"
CONTENT_MODERATOR = "CONTENT_MODERATOR"
ADMIN_PILOTAGE = "ADMIN_PILOTAGE"

ROLE_DEFINITIONS = {
    ULTRA_ADMIN: {
        "label": "Ultra-administrateur",
        # Stored explicitly so a new permission is never silently granted.
        "permissions": list(ALL_PERMISSIONS),
    },
    ADMIN_GENERAL: {
        "label": "Administrateur général",
        "permissions": [
            USERS_READ,
            USERS_UPDATE,
            USERS_SUSPEND,
            SUPPLIERS_READ,
            QUOTES_READ,
            ORDERS_READ,
            ANALYTICS_READ,
            MODERATION_MANAGE,
            SUPPORT_MANAGE,
            SUBSCRIPTIONS_READ,
        ],
    },
    ADMIN_OPERATIONS: {
        "label": "Administrateur opérations",
        "permissions": [
            SUPPLIERS_READ,
            SUPPLIERS_MANAGE,
            PRODUCTS_MANAGE,
            QUOTES_READ,
            QUOTES_MANAGE,
            ORDERS_READ,
            ORDERS_MANAGE,
            CAMPAIGNS_MANAGE,
            MODERATION_MANAGE,
            ANALYTICS_READ,
            COMMISSIONS_READ,
        ],
    },
    ADMIN_SUPPORT: {
        "label": "Administrateur support",
        "permissions": [
            USERS_READ,
            SUPPORT_MANAGE,
            QUOTES_READ,
            ORDERS_READ,
            PRAGMADINARS_READ,
        ],
    },
    ADMIN_FINANCE: {
        "label": "Administrateur finance",
        "permissions": [
            SUBSCRIPTIONS_READ,
            SUBSCRIPTIONS_MANAGE,
            PRAGMADINARS_READ,
            PRAGMADINARS_CREDIT,
            PRAGMADINARS_REFUND,
            REPORTS_GENERATE,
            ANALYTICS_READ,
            COMMISSIONS_READ,
            COMMISSIONS_MANAGE,
        ],
    },
    ANALYST: {
        "label": "Analyste",
        "permissions": [
            ANALYTICS_READ,
            REPORTS_GENERATE,
        ],
    },
    CONTENT_MODERATOR: {
        "label": "Modérateur de contenu",
        "permissions": [
            MODERATION_MANAGE,
        ],
    },
    ADMIN_PILOTAGE: {
        "label": "Administrateur pilotage",
        "permissions": [
            PILOTAGE_ACCESS,
        ],
    },
}

#: Roles that may never be granted through the regular API by a non ultra-admin.
PROTECTED_ROLES = {ULTRA_ADMIN}


def seed_roles():
    """Create or refresh the system roles. Idempotent."""
    from user.models import PlatformRole

    for code, spec in ROLE_DEFINITIONS.items():
        PlatformRole.objects.update_or_create(
            code=code,
            defaults={
                "label": spec["label"],
                "permissions": spec["permissions"],
                "is_system": True,
            },
        )


def active_role_codes(user) -> set[str]:
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    return set(
        user.role_grants.filter(revoked_at__isnull=True).values_list(
            "role__code", flat=True
        )
    )


def user_permissions(user) -> set[str]:
    """Effective permission set. Django superusers map to ULTRA_ADMIN."""
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    if getattr(user, "is_superuser", False):
        return set(ALL_PERMISSIONS)

    from user.models import PlatformRole

    codes = active_role_codes(user)
    if not codes:
        return set()
    if ULTRA_ADMIN in codes:
        return set(ALL_PERMISSIONS)

    perms: set[str] = set()
    for role in PlatformRole.objects.filter(code__in=codes):
        perms.update(role.permissions or [])
    return perms


def is_ultra_admin(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return ULTRA_ADMIN in active_role_codes(user)


def has_permission(user, permission: str) -> bool:
    return permission in user_permissions(user)


def require_permission(user, permission: str):
    """Raise 403 unless the account is active and holds the permission."""
    if not user or not getattr(user, "is_authenticated", False):
        raise PermissionDenied({"error": "Authentification requise.", "code": "unauthenticated"})
    if not user.is_active:
        raise PermissionDenied({"error": "Compte désactivé.", "code": "account_disabled"})
    if not has_permission(user, permission):
        raise PermissionDenied(
            {
                "error": "Vous n'avez pas la permission requise pour cette action.",
                "code": "permission_denied",
                "requiredPermission": permission,
            }
        )
    return True


def require_pricing_manage(user):
    """Tariffs are editable only by ultra-administrators (e.g. owner account)."""
    require_permission(user, PRAGMADINARS_PRICING_MANAGE)
    if not is_ultra_admin(user):
        raise PermissionDenied(
            {
                "error": "Seul l'ultra-administrateur peut modifier les tarifs.",
                "code": "pricing_ultra_admin_only",
            }
        )
    return True


def grant_role(user, role_code: str, *, granted_by=None, reason: str = ""):
    """Grant a role. ULTRA_ADMIN requires an existing ultra-admin as granter."""
    from user.models import PlatformRole, RoleGrant

    if role_code in PROTECTED_ROLES:
        if granted_by is None or not is_ultra_admin(granted_by):
            raise PermissionDenied(
                {
                    "error": "Seul un ultra-administrateur peut attribuer ce rôle.",
                    "code": "protected_role",
                }
            )
    if granted_by is not None and granted_by.pk == getattr(user, "pk", None):
        if role_code in PROTECTED_ROLES:
            raise PermissionDenied(
                {
                    "error": "Un utilisateur ne peut pas s'attribuer ce rôle.",
                    "code": "self_grant_forbidden",
                }
            )

    role = PlatformRole.objects.get(code=role_code)
    existing = RoleGrant.objects.filter(
        user=user, role=role, revoked_at__isnull=True
    ).first()
    if existing:
        return existing
    return RoleGrant.objects.create(
        user=user, role=role, granted_by=granted_by, reason=reason
    )


def revoke_role(user, role_code: str, *, revoked_by=None):
    from user.models import RoleGrant

    grants = RoleGrant.objects.filter(
        user=user, role__code=role_code, revoked_at__isnull=True
    )
    count = grants.count()
    grants.update(revoked_at=timezone.now(), revoked_by=revoked_by)
    return count


def client_ip(request) -> str:
    if request is None:
        return ""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def log_audit(
    actor,
    action: str,
    *,
    entity_type: str = "",
    entity_id: int | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    reason: str = "",
    request=None,
):
    """Append an audit entry. Never raises into the caller's happy path."""
    from user.models import AuditLog

    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value or {},
        new_value=new_value or {},
        reason=reason,
        ip_address=client_ip(request),
    )
