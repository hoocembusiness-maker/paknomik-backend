"""
Subscription plan limits (how many products, tags, paid features, etc.).

Base CRM (companies, contacts, pipeline, activities) is free for every supplier
and is not gated by plan or Pragmadinar balance.

Each plan's rules live in the database (`PlanEntitlement`), not in hardcoded
numbers in the code. Admin can change limits; this file reads them and checks
whether a supplier is allowed to do something this month.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

PLAN_GENEREUX = "genereux"
PLAN_BUSINESS = "business"

# Countable limits
PRODUCTS_MAX = "products.max"
PRODUCT_IMPRESSIONS_MONTHLY = "product_impressions.monthly"
PRODUCT_VISITS_MONTHLY = "product_visits.monthly"
STORE_IMPRESSIONS_MONTHLY = "store_impressions.monthly"
STORE_VISITS_MONTHLY = "store_visits.monthly"

# Feature flags (limit_value 0 = refusé, null = illimité)
FEATURE_CRM = "feature.crm"
FEATURE_DASHBOARD_CURRENT_MONTH = "feature.dashboard_current_month"
FEATURE_DASHBOARD_HISTORY = "feature.dashboard_history"
FEATURE_MARKET_ANALYSIS = "feature.market_analysis"
FEATURE_BENCHMARKING = "feature.benchmarking"
FEATURE_ADVANCED_REPORTS = "feature.advanced_reports"
FEATURE_SPONSORED = "feature.sponsored_promotion"
FEATURE_PAK_DATING = "feature.pak_dating"
FEATURE_PDF_DATASHEET = "feature.pdf_datasheet"
FEATURE_EXTRA_TEMPLATES = "feature.extra_templates"

METERED_KEYS = [
    PRODUCT_IMPRESSIONS_MONTHLY,
    PRODUCT_VISITS_MONTHLY,
    STORE_IMPRESSIONS_MONTHLY,
    STORE_VISITS_MONTHLY,
]

#: (plan, key, label, limit_value) — limit_value None means unlimited.
DEFAULT_ENTITLEMENTS = [
    (PLAN_GENEREUX, PRODUCTS_MAX, "Nombre de produits", 3),
    (PLAN_GENEREUX, PRODUCT_IMPRESSIONS_MONTHLY, "Apparitions de produits / mois", 50),
    (PLAN_GENEREUX, PRODUCT_VISITS_MONTHLY, "Visites de pages produits / mois", 50),
    (PLAN_GENEREUX, STORE_IMPRESSIONS_MONTHLY, "Apparitions de boutique / mois", 0),
    (PLAN_GENEREUX, STORE_VISITS_MONTHLY, "Visites de boutique / mois", 0),
    (PLAN_GENEREUX, FEATURE_CRM, "CRM commercial", None),
    # Intelligence / promo: no free tier — billed in Pragmadinar (catalog).
    (PLAN_GENEREUX, FEATURE_DASHBOARD_CURRENT_MONTH, "Tableau de bord du mois", None),
    (PLAN_GENEREUX, FEATURE_DASHBOARD_HISTORY, "Mois précédents", None),
    (PLAN_GENEREUX, FEATURE_MARKET_ANALYSIS, "Analyse de marché", None),
    (PLAN_GENEREUX, FEATURE_BENCHMARKING, "Benchmarking", None),
    (PLAN_GENEREUX, FEATURE_ADVANCED_REPORTS, "Rapports avancés", None),
    (PLAN_GENEREUX, FEATURE_SPONSORED, "Promotion sponsorisée", None),
    (PLAN_GENEREUX, FEATURE_PAK_DATING, "Pak-dating", None),
    (PLAN_GENEREUX, FEATURE_PDF_DATASHEET, "Fiche technique PDF", None),
    (PLAN_GENEREUX, FEATURE_EXTRA_TEMPLATES, "Modèles supplémentaires", None),
    (PLAN_BUSINESS, PRODUCTS_MAX, "Nombre de produits", None),
    (PLAN_BUSINESS, PRODUCT_IMPRESSIONS_MONTHLY, "Apparitions de produits / mois", None),
    (PLAN_BUSINESS, PRODUCT_VISITS_MONTHLY, "Visites de pages produits / mois", None),
    (PLAN_BUSINESS, STORE_IMPRESSIONS_MONTHLY, "Apparitions de boutique / mois", None),
    (PLAN_BUSINESS, STORE_VISITS_MONTHLY, "Visites de boutique / mois", None),
    (PLAN_BUSINESS, FEATURE_CRM, "CRM commercial", None),
    (PLAN_BUSINESS, FEATURE_DASHBOARD_CURRENT_MONTH, "Tableau de bord du mois", None),
    (PLAN_BUSINESS, FEATURE_PDF_DATASHEET, "Fiche technique PDF", None),
    # Paid in Pragmadinar even for Business: the flag stays enabled, the debit decides.
    (PLAN_BUSINESS, FEATURE_DASHBOARD_HISTORY, "Mois précédents", None),
    (PLAN_BUSINESS, FEATURE_MARKET_ANALYSIS, "Analyse de marché", None),
    (PLAN_BUSINESS, FEATURE_BENCHMARKING, "Benchmarking", None),
    (PLAN_BUSINESS, FEATURE_ADVANCED_REPORTS, "Rapports avancés", None),
    (PLAN_BUSINESS, FEATURE_SPONSORED, "Promotion sponsorisée", None),
    (PLAN_BUSINESS, FEATURE_PAK_DATING, "Pak-dating", None),
    (PLAN_BUSINESS, FEATURE_EXTRA_TEMPLATES, "Modèles supplémentaires", None),
]


class QuotaExceeded(PermissionDenied):
    """Raised when a plan limit blocks an action. Never deletes existing data."""

    def __init__(self, key, limit, used, plan):
        self.key = key
        super().__init__(
            {
                "error": (
                    f"Limite du plan Généreux atteinte ({used}/{limit}). "
                    "Rechargez vos Pragmadinar pour réactiver Paknomik Business."
                ),
                "code": "quota_exceeded",
                "entitlement": key,
                "limit": limit,
                "used": used,
                "plan": plan,
                "upgradePath": "/supplier/wallet",
            }
        )


class FeatureLocked(PermissionDenied):
    def __init__(self, key, plan):
        self.key = key
        super().__init__(
            {
                "error": (
                    "Cette fonctionnalité nécessite Paknomik Business : "
                    "rechargez vos Pragmadinar pour l'activer."
                ),
                "code": "feature_locked",
                "entitlement": key,
                "plan": plan,
                "upgradePath": "/supplier/wallet",
            }
        )


def seed_entitlements():
    from user.models import PlanEntitlement

    for plan, key, label, limit in DEFAULT_ENTITLEMENTS:
        PlanEntitlement.objects.update_or_create(
            plan_code=plan,
            key=key,
            defaults={"label": label, "limit_value": limit, "is_enabled": True},
        )


def available_pragmadinar(supplier) -> Decimal:
    from user.pragmadinar import wallet_payload

    return Decimal(wallet_payload(supplier)["available"])


def plan_code_for(supplier, *, now=None) -> str:
    """There is no monthly subscription: Paknomik Business stays active as long as
    the supplier has Pragmadinar available, then the free Généreux floor applies."""
    return PLAN_BUSINESS if available_pragmadinar(supplier) > 0 else PLAN_GENEREUX


def get_entitlement(supplier, key):
    """Return the PlanEntitlement row for this supplier's plan, or None."""
    from user.models import PlanEntitlement

    return PlanEntitlement.objects.filter(
        plan_code=plan_code_for(supplier), key=key
    ).first()


def limit_for(supplier, key):
    """None = unlimited. 0 = forbidden. Missing row defaults to unlimited."""
    row = get_entitlement(supplier, key)
    if row is None or not row.is_enabled:
        return None if row is None else 0
    return row.limit_value


def has_feature(supplier, key) -> bool:
    limit = limit_for(supplier, key)
    return limit is None or limit > 0


def require_feature(supplier, key):
    if not has_feature(supplier, key):
        raise FeatureLocked(key, plan_code_for(supplier))
    return True


def _period(now=None):
    now = now or timezone.now()
    return now.year, now.month


def usage_for(supplier, key, *, now=None) -> int:
    from user.models import UsageCounter

    year, month = _period(now)
    row = UsageCounter.objects.filter(
        supplier=supplier, key=key, year=year, month=month
    ).first()
    return row.used if row else 0


def quota_snapshot(supplier, key, *, now=None) -> dict:
    """Consumption payload used by the UI to warn before the wall."""
    limit = limit_for(supplier, key)
    used = usage_for(supplier, key, now=now)
    if limit is None:
        return {
            "key": key,
            "used": used,
            "limit": None,
            "unlimited": True,
            "percentUsed": 0,
            "remaining": None,
            "exhausted": False,
        }
    percent = 100 if limit == 0 else min(100, round(used * 100 / limit))
    return {
        "key": key,
        "used": used,
        "limit": limit,
        "unlimited": False,
        "percentUsed": percent,
        "remaining": max(limit - used, 0),
        "exhausted": used >= limit,
    }


def can_consume(supplier, key, amount=1, *, now=None) -> bool:
    limit = limit_for(supplier, key)
    if limit is None:
        return True
    if limit == 0:
        return False
    return usage_for(supplier, key, now=now) + amount <= limit


@transaction.atomic
def consume(
    supplier,
    key,
    amount=1,
    *,
    now=None,
    enforce=True,
    always_track=False,
):
    """Increment a metered counter. Returns False instead of raising when
    `enforce` is False, so analytics logging never breaks a page render.

    When `always_track` is True, usage is recorded even past the plan limit
    (for dashboards while Business promo or unlimited plans are active).
    """
    from user.models import UsageCounter

    limit = limit_for(supplier, key)
    year, month = _period(now)
    counter, _ = UsageCounter.objects.get_or_create(
        supplier=supplier, key=key, year=year, month=month
    )
    counter = UsageCounter.objects.select_for_update().get(pk=counter.pk)

    would_exceed = limit is not None and counter.used + amount > limit
    if would_exceed:
        if always_track:
            UsageCounter.objects.filter(pk=counter.pk).update(used=F("used") + amount)
        if enforce:
            raise QuotaExceeded(key, limit, counter.used, plan_code_for(supplier))
        return False

    UsageCounter.objects.filter(pk=counter.pk).update(used=F("used") + amount)
    return True


def assert_can_add_product(supplier):
    """Généreux floor: 3 active products. Business (Pragmadinar available): no cap."""
    limit = limit_for(supplier, PRODUCTS_MAX)
    if limit is None:
        return True
    current = supplier.products.filter(is_active=True).count()
    if current >= limit:
        raise QuotaExceeded(PRODUCTS_MAX, limit, current, plan_code_for(supplier))
    return True


def entitlements_payload(supplier) -> dict:
    """Everything the supplier UI needs to render quotas and locks."""
    from user.models import PlanEntitlement

    plan = plan_code_for(supplier)
    rows = PlanEntitlement.objects.filter(plan_code=plan)
    features = {}
    for row in rows:
        if row.key.startswith("feature."):
            features[row.key] = row.is_enabled and (
                row.limit_value is None or row.limit_value > 0
            )
    products_limit = limit_for(supplier, PRODUCTS_MAX)
    return {
        "plan": plan,
        "planUntil": None,
        "pragmadinarAvailable": f"{available_pragmadinar(supplier):.2f}",
        "features": features,
        "products": {
            "used": supplier.products.filter(is_active=True).count(),
            "limit": products_limit,
            "unlimited": products_limit is None,
        },
        "quotas": [quota_snapshot(supplier, key) for key in METERED_KEYS],
    }
