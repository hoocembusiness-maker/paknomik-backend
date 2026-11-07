"""
Pragmadinar / Pragmadinar virtual wallet (credits and paid services).

Suppliers buy or spend Pragmadinar for features (boosts, reports, etc.).
Service prices are stored in the database; this file handles balances
and charging the right amount.
"""

from __future__ import annotations

import uuid
from decimal import InvalidOperation, ROUND_CEILING, Decimal

from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from user.platform_config import pragmadinar_rate_dzd, pragmadinar_to_dzd

QUOTE_PUBLIC_COOLDOWN_HOURS = 72
PUBLIC_QUOTE_ENGAGED_CAP = 4

# Listing: « Accéder aux clients ayant un intérêt pour la catégorie » — 1 / accès.
# Used by the public-quote Opportunities unlock. Change the row in admin, not here.
OPPORTUNITY_SERVICE_CODE = "commercial.category_leads"

PLAN_GENEREUX = "genereux"
PLAN_BUSINESS = "business"

ACCESS_UNLIMITED = "unlimited"
ACCESS_PRAGMADINAR = "pragmadinar"
ACCESS_UNAVAILABLE = "unavailable"
ACCESS_AVAILABLE = "available"
ACCESS_UNTIL_APPEARANCE = "until_appearance"
ACCESS_QUOTA = "quota"
ACCESS_BALANCE_POSITIVE = "balance_positive"

UNIT_FEATURE = "feature"
UNIT_REQUEST = "request"
UNIT_VISIT = "visit"
UNIT_VIEW = "view"
UNIT_ONE_SHOT = "one_shot"
UNIT_MONTH_QUOTA = "month_quota"

PLANS = (
    {
        "code": PLAN_GENEREUX,
        "name": "Paknomik Généreux",
        "monthly_price_dzd": Decimal("0"),
        "is_default": True,
    },
    {
        "code": PLAN_BUSINESS,
        "name": "Paknomik Business",
        # No subscription: Business is active while the Pragmadinar balance lasts.
        "monthly_price_dzd": Decimal("0"),
        "is_default": False,
    },
)


LISTING_CLASS_LABELS = {
    "commercial": "Activité commerciale",
    "promotion": "Promotion",
    "visibility": "Visibilité",
}
LISTING_CLASS_ORDER = tuple(LISTING_CLASS_LABELS)

ACCESS_LABELS = {
    ACCESS_UNLIMITED: "Illimité",
    ACCESS_PRAGMADINAR: "Pragmadinar",
    ACCESS_UNAVAILABLE: "Non disponible",
    ACCESS_AVAILABLE: "Disponible",
    ACCESS_UNTIL_APPEARANCE: "Illimité jusqu'à épuisement du nombre d'apparitions",
    ACCESS_BALANCE_POSITIVE: "Si solde > 0",
}


def access_label(access, quota=None) -> str:
    if access == ACCESS_QUOTA:
        return str(quota) if quota is not None else "Quota"
    return ACCESS_LABELS.get(access, access)


def _svc(
    code,
    service_class,
    label,
    cost,
    unit,
    unit_quantity,
    business_access,
    genereux_access,
    genereux_quota=None,
    *,
    rank=None,
    unit_label="",
):
    if code.startswith("intel."):
        wallet_category = "analytics"
    elif code.startswith("promo.") or code in {
        "commercial.category_leads",
        "commercial.pak_dating",
    }:
        wallet_category = "promotion"
    else:
        wallet_category = "visibility"
    return {
        "code": code,
        "service_class": service_class,
        "wallet_category": wallet_category,
        "label": label,
        "cost_pragmadinar": Decimal(str(cost)),
        "unit": unit,
        "unit_quantity": unit_quantity,
        "business_access": business_access,
        "genereux_access": genereux_access,
        "genereux_quota": genereux_quota,
        "price_rank": rank,
        "unit_label": unit_label,
    }


_PAID = (ACCESS_PRAGMADINAR, ACCESS_UNAVAILABLE)
_FREE = (ACCESS_UNLIMITED, ACCESS_UNTIL_APPEARANCE)
_REPORT = "Ouverture (lecture illimitée)"

# Official price list (listing tarifaire). Every price is per single unit
# (unit_quantity = 1); the admin can still edit rows afterwards.
CATALOG_SERVICES = (
    # ── Activité commerciale ────────────────────────────────────────────────
    _svc("commercial.quote_request", "commercial", "Demande de devis", 0, UNIT_FEATURE, 1, *_FREE, rank=1, unit_label="Demande"),
    _svc("commercial.messaging", "commercial", "Messagerie illimitée", 0, UNIT_FEATURE, 1, *_FREE, rank=1, unit_label="Nouveau contact"),
    _svc("commercial.orders", "commercial", "Réception de commandes", 0, UNIT_FEATURE, 1, *_FREE, rank=1, unit_label="Commande"),
    _svc("intel.report_annual", "commercial", "Rapport annuel PAKNOMIK", 40, UNIT_ONE_SHOT, 1, *_PAID, rank=1, unit_label=_REPORT),
    _svc("intel.report_semiannual", "commercial", "Rapport semestriel PAKNOMIK", 20, UNIT_ONE_SHOT, 1, *_PAID, rank=2, unit_label=_REPORT),
    _svc("intel.report_quarterly", "commercial", "Rapport trimestriel PAKNOMIK", 10, UNIT_ONE_SHOT, 1, *_PAID, rank=3, unit_label=_REPORT),
    _svc("intel.report_activity_annual", "commercial", "Rapport annuel d'activité", 8, UNIT_ONE_SHOT, 1, *_PAID, rank=4, unit_label=_REPORT),
    _svc("intel.market_performance", "commercial", "Comparatif de performance du marché", 5, UNIT_REQUEST, 1, *_PAID, rank=5, unit_label="Consultation"),
    _svc("intel.report_activity_semiannual", "commercial", "Rapport semestriel d'activité", "4.5", UNIT_ONE_SHOT, 1, *_PAID, rank=6, unit_label=_REPORT),
    _svc("intel.report_monthly", "commercial", "Rapport mensuel PAKNOMIK", 2, UNIT_ONE_SHOT, 1, *_PAID, rank=10, unit_label=_REPORT),
    _svc("intel.market_period", "commercial", "Analyse de marché — période sélectionnée", 3, UNIT_REQUEST, 1, *_PAID, rank=8, unit_label="Consultation"),
    _svc("intel.market_category", "commercial", "Analyse de marché — catégorie sélectionnée", 3, UNIT_REQUEST, 1, *_PAID, rank=8, unit_label="Consultation"),
    _svc("intel.benchmark_supplier", "commercial", "Benchmarking anonyme fournisseur", 2, UNIT_REQUEST, 1, *_PAID, rank=10, unit_label="Consultation"),
    _svc("intel.market_overview", "commercial", "Analyse de marché — vue d'ensemble", 2, UNIT_REQUEST, 1, *_PAID, rank=10, unit_label="Consultation"),
    _svc("intel.report_activity_quarterly", "commercial", "Rapport trimestriel d'activité", 4, UNIT_ONE_SHOT, 1, *_PAID, rank=7, unit_label=_REPORT),
    _svc("intel.performance_compare", "commercial", "Comparatif de performance (mois/catégorie/produits)", "0.8", UNIT_REQUEST, 1, *_PAID, rank=13, unit_label="Consultation"),
    _svc("intel.report_activity_monthly", "commercial", "Rapport mensuel d'activité", "0.8", UNIT_ONE_SHOT, 1, *_PAID, rank=13, unit_label=_REPORT),
    _svc("intel.dashboard_previous", "commercial", "Tableau de bord période sélectionnée", "0.2", UNIT_REQUEST, 1, *_PAID, rank=15, unit_label="Consultation"),
    _svc("intel.dashboard_current", "commercial", "Tableau de bord du mois en cours — 1j / 7j / 30j", "0.1", UNIT_REQUEST, 1, *_PAID, rank=16, unit_label="Consultation"),
    # Still billed by existing features but absent from the latest listing:
    # kept active (with their current price) so nothing breaks; no rank.
    _svc("intel.report_detailed", "commercial", "Rapport détaillé de performance", 8, UNIT_REQUEST, 1, *_PAID, unit_label="Consultation"),
    _svc("commercial.product_extra_photo", "commercial", "Photo produit supplémentaire (4e et suivantes)", "0.1", UNIT_ONE_SHOT, 1, ACCESS_PRAGMADINAR, ACCESS_PRAGMADINAR, unit_label="Photo"),
    _svc("commercial.pak_dating", "commercial", "Pak-dating (mise en relation après consentement)", 10, UNIT_REQUEST, 1, *_PAID, unit_label="Mise en relation"),
    # ── Promotion ───────────────────────────────────────────────────────────
    _svc("visibility.vitrine_mega_search", "promotion", "Vitrine Mega dans les résultats de recherche", 1, UNIT_VIEW, 1, *_PAID, rank=1, unit_label="Visite qualifiée"),
    _svc("promo.proposal_template", "promotion", "Nouveau modèle de proposition", 1, UNIT_ONE_SHOT, 1, *_PAID, rank=1, unit_label="Modèle"),
    _svc("commercial.category_leads", "promotion", "Accéder aux clients ayant un intérêt pour la catégorie du fournisseur", 1, UNIT_REQUEST, 1, *_PAID, rank=1, unit_label="Accès"),
    _svc("promo.priority_subcategory", "promotion", "Apparition prioritaire dans la sous-catégorie", "0.8", UNIT_VIEW, 1, *_PAID, rank=4, unit_label="Vue"),
    _svc("visibility.card_horizontal_colored_search", "promotion", "Carte de produit horizontale colorée dans les résultats de recherche", "0.6", UNIT_VIEW, 1, *_PAID, rank=5, unit_label="Visite qualifiée"),
    _svc("visibility.card_passport_colored_search", "promotion", "Carte de produit (passport) colorée dans les résultats de recherche", "0.6", UNIT_VIEW, 1, *_PAID, rank=5, unit_label="Visite qualifiée"),
    _svc("visibility.card_horizontal_search", "promotion", "Carte de produit horizontale dans les résultats de recherche", "0.5", UNIT_VIEW, 1, *_PAID, rank=7, unit_label="Visite qualifiée"),
    _svc("visibility.vitrine_central_search", "promotion", "Vitrine centrale dans les résultats de recherche", "0.5", UNIT_VIEW, 1, *_PAID, rank=7, unit_label="Visite qualifiée"),
    _svc("promo.sponsored_banner_conversation", "promotion", "Bannière verticale sponsorisée (conversation)", "0.5", UNIT_VIEW, 1, *_PAID, rank=7, unit_label="Visite qualifiée"),
    _svc("visibility.card_passport_search", "promotion", "Carte de produit (passport) dans les résultats de recherche", "0.4", UNIT_VIEW, 1, *_PAID, rank=10, unit_label="Visite qualifiée"),
    _svc("visibility.vitrine_extra_search", "promotion", "Vitrine extra dans les résultats de recherche", "0.4", UNIT_VIEW, 1, *_PAID, rank=10, unit_label="Visite qualifiée"),
    _svc("promo.keyword_reservation", "promotion", "Réservation de mot-clé", "0.3", UNIT_VIEW, 1, *_PAID, rank=12, unit_label="Vue"),
    _svc("promo.sponsored_banner", "promotion", "Bannière horizontale sponsorisée (résultat de recherche)", "0.2", UNIT_VIEW, 1, *_PAID, rank=13, unit_label="Visite qualifiée"),
    _svc("promo.quote_template", "promotion", "Nouveau modèle devis", 5, UNIT_ONE_SHOT, 1, *_PAID, unit_label="Modèle"),
    # ── Visibilité ──────────────────────────────────────────────────────────
    _svc("visibility.card_horizontal_colored", "visibility", "Carte de produit horizontale colorée (dans la boutique)", "0.6", UNIT_VISIT, 1, *_PAID, rank=1, unit_label="Vue qualifiée"),
    _svc("visibility.card_horizontal", "visibility", "Carte de produit horizontale (dans la boutique)", "0.5", UNIT_VISIT, 1, *_PAID, rank=2, unit_label="Vue qualifiée"),
    _svc("visibility.vitrine_mega", "visibility", "Vitrine Mega (dans la boutique)", "0.5", UNIT_VISIT, 1, *_PAID, rank=2, unit_label="Visite qualifiée"),
    _svc("visibility.card_passport_colored", "visibility", "Carte de produit (passport) colorée (dans la boutique)", "0.45", UNIT_VISIT, 1, *_PAID, rank=4, unit_label="Vue qualifiée"),
    _svc("visibility.vitrine_central", "visibility", "Vitrine centrale (dans la boutique)", "0.45", UNIT_VISIT, 1, *_PAID, rank=4, unit_label="Visite qualifiée"),
    _svc("visibility.card_passport", "visibility", "Carte de produit (passport) (dans la boutique)", "0.4", UNIT_VISIT, 1, *_PAID, rank=6, unit_label="Vue qualifiée"),
    _svc("visibility.vitrine_extra", "visibility", "Vitrine extra (dans la boutique)", "0.4", UNIT_VISIT, 1, *_PAID, rank=6, unit_label="Visite qualifiée"),
    _svc("visibility.store_favorited", "visibility", "Boutique ajoutée aux favoris", "0.4", UNIT_FEATURE, 1, *_PAID, rank=6, unit_label="Favori"),
    _svc("visibility.product_favorited", "visibility", "Produit ajouté aux favoris", "0.4", UNIT_FEATURE, 1, *_PAID, rank=6, unit_label="Favori"),
    _svc("visibility.store_visit", "visibility", "Visite de boutique", "0.3", UNIT_VISIT, 1, *_PAID, rank=10, unit_label="Visite qualifiée"),
    _svc("visibility.product_visit", "visibility", "Visite de page produit", "0.3", UNIT_VISIT, 1, *_PAID, rank=10, unit_label="Visite qualifiée"),
    _svc("visibility.social_links", "visibility", "Liens des réseaux sociaux sous l'onglet « À propos »", "0.15", UNIT_VISIT, 1, *_PAID, rank=12, unit_label="Clic"),
    _svc("visibility.search_store", "visibility", "Apparition de boutique dans les résultats de recherche", "0.1", UNIT_VIEW, 1, *_PAID, rank=13, unit_label="Apparition"),
    _svc("visibility.search_products", "visibility", "Apparition de produits dans les résultats de recherche", "0.1", UNIT_VIEW, 1, *_PAID, rank=13, unit_label="Apparition"),
    _svc("visibility.pdf_download", "visibility", "Téléchargement de fiche technique PDF", "0.1", UNIT_FEATURE, 1, *_PAID, rank=13, unit_label="Téléchargement"),
    _svc("visibility.product_count", "visibility", "Nombre de produits", 0, UNIT_MONTH_QUOTA, 1, ACCESS_BALANCE_POSITIVE, ACCESS_UNAVAILABLE, rank=16, unit_label="Produit"),
    _svc("visibility.vitrine_standard", "visibility", "Vitrine standard (dans la boutique)", 0, UNIT_FEATURE, 1, ACCESS_UNLIMITED, ACCESS_UNAVAILABLE, rank=16, unit_label="Vue"),
)

# Present in the catalog for metering, absent from the supplier price list.
PRICE_LIST_OMITTED = frozenset({
    "visibility.store_favorited",
    "visibility.product_favorited",
})

# Removed from the listing and not billed by any feature: deactivated on seed.
RETIRED_SERVICE_CODES = (
    "visibility.product_compared",
    "visibility.product_in_comparison",
)

# Visibility, orders, quotes, favorites, messaging and sponsored placements
# stay available. They no longer deduct Pragmadinar.
_UNBILLED_EXPLICIT = {
    "commercial.quote_request",
    "commercial.messaging",
    "commercial.orders",
    "promo.priority_subcategory",
    "promo.keyword_reservation",
    "promo.sponsored_banner",
    "promo.sponsored_banner_conversation",
}
UNBILLED_SERVICE_CODES = frozenset(
    row["code"]
    for row in CATALOG_SERVICES
    if row["code"].startswith("visibility.") or row["code"] in _UNBILLED_EXPLICIT
)
_CATALOG_LIST_PRICES = {
    row["code"]: Decimal(str(row["cost_pragmadinar"])) for row in CATALOG_SERVICES
}


def on_price_list(service) -> bool:
    code = getattr(service, "code", None)
    rank = getattr(service, "price_rank", None)
    return rank is not None and code not in PRICE_LIST_OMITTED


# Strategy objectives follow the tariff classes, not the billing wallet.
# Search vitrines and search cards are Promotion on the price list.
_TARIFF_CLASS_WALLET = {
    "visibility": "visibility",
    "promotion": "promotion",
    "commercial": "analytics",
}
_CATALOG_BY_CODE = {row["code"]: row for row in CATALOG_SERVICES}


def strategy_wallet_for_code(code: str) -> str | None:
    row = _CATALOG_BY_CODE.get(code)
    if not row or row["price_rank"] is None or code in PRICE_LIST_OMITTED:
        return None
    return _TARIFF_CLASS_WALLET.get(row["service_class"])


def tariff_display_cost(service) -> Decimal:
    """Official price-list amount.

    Unbilled services are stored at 0 so usage does not debit. The listing
    still shows the catalog rate.
    """
    if getattr(service, "code", None) in UNBILLED_SERVICE_CODES:
        listed = _CATALOG_LIST_PRICES.get(service.code)
        if listed is not None:
            return listed
    return Decimal(str(service.cost_pragmadinar))


def plain_amount(value) -> str:
    """Decimal → plain French text, never scientific notation (40, 4,5, 0,45)."""
    amount = Decimal(str(value)).normalize()
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


_UNIT_NOUNS = {
    UNIT_VISIT: "visite",
    UNIT_VIEW: "vue",
    UNIT_REQUEST: "requête",
    UNIT_ONE_SHOT: "achat",
    UNIT_MONTH_QUOTA: "mois",
}


def format_cost_label(cost, unit, unit_quantity, unit_label=""):
    cost = Decimal(str(cost))
    if cost == 0:
        return "0"
    noun = (unit_label or _UNIT_NOUNS.get(unit, "")).strip()
    if unit_label:
        noun = noun[:1].lower() + noun[1:]
    quantity = int(unit_quantity or 1)
    if quantity > 1:
        per = f"{quantity} {noun}s" if noun and not noun.endswith("s") else f"{quantity} {noun}"
    else:
        per = noun
    price = f"{plain_amount(cost)} Pragmadinar"
    return f"{price} / {per}" if per else price


class InsufficientPragmadinar(ValidationError):
    default_detail = "Solde Pragmadinar insuffisant."
    default_code = "insufficient_pragmadinars"

    def __init__(self, required, balance, recharge_path="/supplier/wallet"):
        self.required = required
        self.balance = balance
        self.recharge_path = recharge_path
        super().__init__(
            {
                "error": (
                    f"Solde insuffisant : {required} Pragmadinar requis, "
                    f"{balance} disponible."
                ),
                # Legacy code kept alongside the new one so existing clients
                # keep recognising the error while the frontend migrates.
                "code": "insufficient_pragmadinars",
                "legacyCode": "insufficient_pragmadinar",
                "required": str(required),
                "balance": str(balance),
                "requiredDzd": str(pragmadinar_to_dzd(required)),
                "rechargePath": recharge_path,
            }
        )


#: Backwards/forwards compatible alias — the official currency name is Pragmadinar.
InsufficientPragmadinars = InsufficientPragmadinar


class ServiceUnavailableForPlan(PermissionDenied):
    def __init__(self, service_label, required_plan="business"):
        super().__init__(
            {
                "error": (
                    f"« {service_label} » nécessite Paknomik Business, actif tant que "
                    "votre solde Pragmadinar est positif. Rechargez pour y accéder."
                ),
                "code": "plan_unavailable",
                "requiredPlan": required_plan,
                "rechargePath": "/supplier/wallet",
            }
        )


def get_supplier_plan_code(supplier):
    from user.entitlements import plan_code_for

    return plan_code_for(supplier)


def get_or_create_wallet(supplier, category="visibility"):
    from user.models import PragmadinarWallet

    wallet, _ = PragmadinarWallet.objects.get_or_create(
        supplier=supplier, category=category
    )
    return wallet


def get_service(code):
    from user.models import PragmadinarService

    try:
        return PragmadinarService.objects.get(code=code, is_active=True)
    except PragmadinarService.DoesNotExist as exc:
        raise ValidationError(
            {"error": f"Service tarifaire inconnu : {code}", "code": "unknown_service"}
        ) from exc


def service_access_for_plan(service, plan_code):
    """Prepaid model: pragmadinar-priced services are available to any supplier
    with balance; plan columns in the catalog describe free-tier quotas only."""
    if service.business_access == ACCESS_PRAGMADINAR:
        return ACCESS_PRAGMADINAR
    if plan_code == PLAN_BUSINESS:
        return service.business_access
    return service.genereux_access


def assert_service_available(supplier, service):
    access = service_access_for_plan(service, get_supplier_plan_code(supplier))
    if access == ACCESS_UNAVAILABLE:
        raise ServiceUnavailableForPlan(service.label)
    return access


def _new_reference(prefix="PKO"):
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _supplier_balance_total(supplier):
    from django.db.models import Sum

    from user.models import PragmadinarWallet

    total = PragmadinarWallet.objects.filter(supplier=supplier).aggregate(
        total=Sum("balance")
    )["total"]
    return total or Decimal("0")


def _cover_debit_from_other_wallets(wallets, target, amount, *, actor=None):
    """Move free balance into the service wallet so one supplier total can pay any remaining service."""
    need = amount - (target.balance - target.held_total)
    if need <= 0:
        return
    for source in wallets:
        if source.pk == target.pk:
            continue
        free = source.balance - source.held_total
        if free <= 0:
            continue
        move = min(free, need)
        _record_wallet_transfer(source, target, move, actor=actor)
        need -= move
        if need <= 0:
            return


def _existing_entry(supplier, idempotency_key):
    from user.models import PragmadinarLedger

    if not idempotency_key:
        return None
    return PragmadinarLedger.objects.filter(
        wallet__supplier=supplier, idempotency_key=idempotency_key
    ).first()


@transaction.atomic
def debit_service(
    supplier,
    service_code,
    *,
    ref_type="",
    ref_id=None,
    reason="",
    idempotency_key="",
    campaign=None,
    wallet_category=None,
    actor=None,
    refundable=False,
    quantity=1,
):
    """Atomic debit priced from the catalog row.

    The wallet row is locked for the whole transaction so two concurrent
    requests can never spend the same balance. Passing the same
    `idempotency_key` twice returns the first transaction instead of debiting
    again.
    """
    from user.models import PragmadinarLedger, PragmadinarWallet

    if service_code in UNBILLED_SERVICE_CODES:
        return None

    service = get_service(service_code)
    assert_service_available(supplier, service)
    amount = service.cost_pragmadinar * Decimal(str(quantity))
    category = wallet_category or service.wallet_category
    get_or_create_wallet(supplier, category=category)
    wallets = list(
        PragmadinarWallet.objects.select_for_update().filter(supplier=supplier)
    )
    wallet = next(row for row in wallets if row.category == category)
    balance_before = sum((row.balance for row in wallets), Decimal("0"))

    replay = _existing_entry(supplier, idempotency_key)
    if replay is not None:
        return replay

    if amount > 0:
        _cover_debit_from_other_wallets(wallets, wallet, amount, actor=actor)
        available = wallet.balance - wallet.held_total
        if available < amount:
            total_available = sum(
                (row.balance - row.held_total for row in wallets), Decimal("0")
            )
            raise InsufficientPragmadinar(amount, total_available)
        wallet.balance -= amount
        wallet.spent_total += amount
        wallet.save(update_fields=["balance", "spent_total", "updated_at"])

    return PragmadinarLedger.objects.create(
        wallet=wallet,
        service=service,
        campaign=campaign,
        entry_type=PragmadinarLedger.EntryType.SPEND,
        status=PragmadinarLedger.Status.POSTED,
        amount=-amount,
        balance_before=balance_before,
        balance_after=balance_before - amount,
        rate_dzd=pragmadinar_rate_dzd(),
        amount_dzd=pragmadinar_to_dzd(amount),
        reference=_new_reference(),
        idempotency_key=idempotency_key,
        reason=reason or service.label,
        ref_type=ref_type,
        ref_id=ref_id,
        refundable=refundable,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


@transaction.atomic
def hold_service(
    supplier,
    service_code,
    *,
    ref_type="",
    ref_id=None,
    reason="",
    idempotency_key="",
    actor=None,
    wallet_category=None,
    quantity=1,
    amount=None,
):
    """Reserve the price without spending it.

    Used by Pak-dating: the amount is only definitively debited once the buyer
    answers, and released automatically on refusal or expiry.

    `amount` reserves a catalog price even when the service is unbilled for
    live usage. Without it, unbilled services are not held.
    """
    from user.models import PragmadinarLedger, PragmadinarWallet

    if amount is None and service_code in UNBILLED_SERVICE_CODES:
        return None

    service = get_service(service_code)
    assert_service_available(supplier, service)
    if amount is None:
        amount = service.cost_pragmadinar * Decimal(str(quantity))
    else:
        amount = Decimal(str(amount))
    if amount <= 0:
        return None
    wallet = get_or_create_wallet(
        supplier, category=wallet_category or service.wallet_category
    )
    wallet = PragmadinarWallet.objects.select_for_update().get(pk=wallet.pk)

    replay = _existing_entry(supplier, idempotency_key)
    if replay is not None:
        return replay

    available = wallet.balance - wallet.held_total
    if amount > available:
        raise InsufficientPragmadinar(amount, available)
    wallet.held_total += amount
    wallet.save(update_fields=["held_total", "updated_at"])

    return PragmadinarLedger.objects.create(
        wallet=wallet,
        service=service,
        entry_type=PragmadinarLedger.EntryType.HOLD,
        status=PragmadinarLedger.Status.HELD,
        amount=-amount,
        rate_dzd=pragmadinar_rate_dzd(),
        amount_dzd=pragmadinar_to_dzd(amount),
        reference=_new_reference("HLD"),
        idempotency_key=idempotency_key,
        reason=reason or f"Réservation — {service.label}",
        ref_type=ref_type,
        ref_id=ref_id,
        refundable=True,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


@transaction.atomic
def settle_hold(hold_entry, *, reason=""):
    """Turn a reservation into a real debit (buyer consented)."""
    from user.models import PragmadinarLedger, PragmadinarWallet

    if hold_entry.status != PragmadinarLedger.Status.HELD:
        return hold_entry
    amount = abs(hold_entry.amount)
    wallet = PragmadinarWallet.objects.select_for_update().get(pk=hold_entry.wallet_id)
    wallet.held_total = max(wallet.held_total - amount, Decimal("0"))
    wallet.balance -= amount
    wallet.spent_total += amount
    wallet.save(
        update_fields=["held_total", "balance", "spent_total", "updated_at"]
    )
    hold_entry.entry_type = PragmadinarLedger.EntryType.SPEND
    hold_entry.status = PragmadinarLedger.Status.POSTED
    if reason:
        hold_entry.reason = reason
    hold_entry.save(update_fields=["entry_type", "status", "reason"])
    return hold_entry


@transaction.atomic
def release_hold(hold_entry, *, reason="Réservation libérée"):
    """Release a reservation (refused or expired). No balance movement."""
    from user.models import PragmadinarLedger, PragmadinarWallet

    if hold_entry.status != PragmadinarLedger.Status.HELD:
        return hold_entry
    amount = abs(hold_entry.amount)
    wallet = PragmadinarWallet.objects.select_for_update().get(pk=hold_entry.wallet_id)
    wallet.held_total = max(wallet.held_total - amount, Decimal("0"))
    wallet.save(update_fields=["held_total", "updated_at"])
    hold_entry.status = PragmadinarLedger.Status.RELEASED
    hold_entry.save(update_fields=["status"])
    PragmadinarLedger.objects.create(
        wallet=wallet,
        service=hold_entry.service,
        entry_type=PragmadinarLedger.EntryType.RELEASE,
        status=PragmadinarLedger.Status.POSTED,
        amount=Decimal("0"),
        rate_dzd=hold_entry.rate_dzd,
        amount_dzd=Decimal("0"),
        reference=_new_reference("REL"),
        reason=reason,
        ref_type=hold_entry.ref_type,
        ref_id=hold_entry.ref_id,
    )
    # Existing holds are intentionally left on the legacy wallet by the
    # migration. Once released, move the newly available amount into the
    # functional wallet chosen for that service so it remains spendable.
    if wallet.category == "legacy" and hold_entry.service_id:
        category = hold_entry.service.wallet_category
        target = get_or_create_wallet(wallet.supplier, category=category)
        target = PragmadinarWallet.objects.select_for_update().get(pk=target.pk)
        _record_wallet_transfer(wallet, target, amount)
    return hold_entry


@transaction.atomic
def refund_entry(entry, *, actor=None, reason="Remboursement technique"):
    """Technical refund of a posted debit. Requires `pragmadinars.refund`.

    A losing proposal is never a refund reason: the service consumed was the
    access to the request, not the win.
    """
    from user.models import PragmadinarLedger, PragmadinarWallet

    if entry.amount >= 0:
        raise ValidationError(
            {"error": "Seul un débit peut être remboursé.", "code": "not_a_debit"}
        )
    if entry.status == PragmadinarLedger.Status.REVERSED:
        raise ValidationError(
            {"error": "Transaction déjà remboursée.", "code": "already_refunded"}
        )
    amount = abs(entry.amount)
    wallet = PragmadinarWallet.objects.select_for_update().get(pk=entry.wallet_id)
    wallet.balance += amount
    wallet.spent_total = max(wallet.spent_total - amount, Decimal("0"))
    wallet.save(update_fields=["balance", "spent_total", "updated_at"])
    entry.status = PragmadinarLedger.Status.REVERSED
    entry.save(update_fields=["status"])
    return PragmadinarLedger.objects.create(
        wallet=wallet,
        service=entry.service,
        campaign=entry.campaign,
        entry_type=PragmadinarLedger.EntryType.REFUND,
        status=PragmadinarLedger.Status.POSTED,
        amount=amount,
        rate_dzd=entry.rate_dzd,
        amount_dzd=pragmadinar_to_dzd(amount),
        reference=_new_reference("RFD"),
        reason=reason,
        ref_type=entry.ref_type,
        ref_id=entry.ref_id,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


WELCOME_GRANT_IDEMPOTENCY_PREFIX = "bootstrap:grant200:v1:"
LEGACY_WELCOME_GRANT_PREFIX = "bootstrap:grant500:v1:"
PREVIOUS_WELCOME_GRANT_PREFIX = "bootstrap:grant100:v1:"
BALANCE_RESET_PREFIX = "admin:balance-reset-200:v1:"
WELCOME_BALANCE = Decimal("200")


def _welcome_grant_already_applied(supplier) -> bool:
    from django.db.models import Q

    from user.models import PragmadinarLedger

    prefixes = (
        WELCOME_GRANT_IDEMPOTENCY_PREFIX,
        PREVIOUS_WELCOME_GRANT_PREFIX,
        LEGACY_WELCOME_GRANT_PREFIX,
    )
    query = Q()
    for prefix in prefixes:
        query |= Q(idempotency_key__startswith=f"{prefix}{supplier.pk}")
    return PragmadinarLedger.objects.filter(
        query,
        wallet__supplier=supplier,
        entry_type=PragmadinarLedger.EntryType.GRANT,
    ).exists()


@transaction.atomic
def normalize_legacy_welcome_grant(supplier):
    """
    Reduce one-time bootstrap:grant500 welcome credits to the current platform
    welcome amount (100 by default). Idempotent once the ledger row is at target.
    """
    from user.models import PragmadinarLedger, PragmadinarWallet
    from user.platform_config import supplier_welcome_pragmadinar

    target = supplier_welcome_pragmadinar()
    if target <= 0:
        return None

    wallet = get_or_create_wallet(supplier, category="legacy")
    legacy_key = f"{LEGACY_WELCOME_GRANT_PREFIX}{supplier.pk}"
    entry = (
        PragmadinarLedger.objects.filter(
            wallet=wallet,
            idempotency_key=legacy_key,
            entry_type=PragmadinarLedger.EntryType.GRANT,
            status=PragmadinarLedger.Status.POSTED,
        )
        .order_by("pk")
        .first()
    )
    if entry is None or entry.amount <= target:
        return None

    excess = entry.amount - target
    wallet = PragmadinarWallet.objects.select_for_update().get(pk=wallet.pk)
    entry.amount = target
    entry.amount_dzd = pragmadinar_to_dzd(target)
    entry.idempotency_key = f"{WELCOME_GRANT_IDEMPOTENCY_PREFIX}{supplier.pk}"
    entry.reason = "Crédit de bienvenue Pragmadinar"
    entry.save(update_fields=["amount", "amount_dzd", "idempotency_key", "reason"])

    wallet.balance = max(wallet.balance - excess, Decimal("0"))
    wallet.granted_total = max(wallet.granted_total - excess, Decimal("0"))
    wallet.save(update_fields=["balance", "granted_total", "updated_at"])
    return entry


WELCOME_SPLIT_PERCENT = (("visibility", 50), ("analytics", 15), ("promotion", 35))


def _welcome_split(amount: Decimal) -> list[tuple[str, Decimal]]:
    """Visibility absorbs the rounding remainder so the parts always sum to amount."""
    parts = [
        (category, (amount * percent / 100).quantize(Decimal("0.01")))
        for category, percent in WELCOME_SPLIT_PERCENT[1:]
    ]
    first = WELCOME_SPLIT_PERCENT[0][0]
    return [(first, amount - sum(value for _, value in parts)), *parts]


@transaction.atomic
def grant_supplier_welcome_pragmadinar(supplier, *, actor=None):
    """Idempotent welcome credit, split 50/15/35 across visibility/analytics/promotion."""
    from user.models import PragmadinarLedger
    from user.platform_config import supplier_welcome_pragmadinar

    amount = supplier_welcome_pragmadinar()
    if amount <= 0:
        return None

    if _welcome_grant_already_applied(supplier):
        return None

    base_key = f"{WELCOME_GRANT_IDEMPOTENCY_PREFIX}{supplier.pk}"
    entries = []
    for category, part in _welcome_split(Decimal(amount)):
        if part <= 0:
            continue
        entries.append(credit_wallet(
            supplier,
            part,
            reason="Crédit de bienvenue Pragmadinar",
            entry_type=PragmadinarLedger.EntryType.GRANT,
            idempotency_key=base_key if category == "visibility" else f"{base_key}:{category}",
            actor=actor,
            wallet_category=category,
        ))
    return entries


@transaction.atomic
def credit_wallet(
    supplier,
    amount,
    *,
    reason="Recharge",
    ref_type="",
    ref_id=None,
    entry_type=None,
    idempotency_key="",
    actor=None,
    wallet_category="visibility",
):
    from user.models import PragmadinarLedger, PragmadinarWallet

    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValidationError({"error": "Le montant doit être positif."})
    wallet = get_or_create_wallet(supplier, category=wallet_category)
    wallet = PragmadinarWallet.objects.select_for_update().get(pk=wallet.pk)

    replay = _existing_entry(supplier, idempotency_key)
    if replay is not None:
        return replay

    kind = entry_type or PragmadinarLedger.EntryType.PURCHASE
    balance_before = _supplier_balance_total(supplier)
    wallet.balance += amount
    fields = ["balance", "updated_at"]
    if kind == PragmadinarLedger.EntryType.GRANT:
        wallet.granted_total += amount
        fields.append("granted_total")
    elif kind == PragmadinarLedger.EntryType.PURCHASE:
        wallet.purchased_total += amount
        fields.append("purchased_total")
    wallet.save(update_fields=fields)

    return PragmadinarLedger.objects.create(
        wallet=wallet,
        service=None,
        entry_type=kind,
        status=PragmadinarLedger.Status.POSTED,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_before + amount,
        rate_dzd=pragmadinar_rate_dzd(),
        amount_dzd=pragmadinar_to_dzd(amount),
        reference=_new_reference("CRD"),
        idempotency_key=idempotency_key,
        reason=reason,
        ref_type=ref_type,
        ref_id=ref_id,
        refundable=False,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


WALLET_CATEGORIES = ("visibility", "analytics", "promotion")


def service_cost_for_quantity(service, quantity):
    """Price objective quantities with the same catalog unit used by billing."""
    quantity = Decimal(str(quantity))
    unit_quantity = max(int(service.unit_quantity or 1), 1)
    billable_units = (quantity / Decimal(unit_quantity)).quantize(
        Decimal("1"), rounding=ROUND_CEILING
    )
    return service.cost_pragmadinar * billable_units


def _record_wallet_transfer(source, destination, amount, *, actor=None):
    from user.models import PragmadinarLedger

    reference = _new_reference("TRF")
    reason = f"Réaffectation : {source.category} → {destination.category}"
    amount_dzd = pragmadinar_to_dzd(amount)
    rate = pragmadinar_rate_dzd()

    source.balance -= amount
    destination.balance += amount
    source.save(update_fields=["balance", "updated_at"])
    destination.save(update_fields=["balance", "updated_at"])

    common = {
        "entry_type": PragmadinarLedger.EntryType.TRANSFER,
        "status": PragmadinarLedger.Status.POSTED,
        "rate_dzd": rate,
        "amount_dzd": amount_dzd,
        "reference": reference,
        "ref_type": "wallet_transfer",
        "refundable": False,
        "created_by": actor if getattr(actor, "is_authenticated", False) else None,
    }
    PragmadinarLedger.objects.create(
        wallet=source,
        amount=-amount,
        idempotency_key=f"{reference}:out",
        reason=reason,
        ref_id=destination.pk,
        **common,
    )
    PragmadinarLedger.objects.create(
        wallet=destination,
        amount=amount,
        idempotency_key=f"{reference}:in",
        reason=reason,
        ref_id=source.pk,
        **common,
    )


@transaction.atomic
def transfer_wallet_balance(supplier, source_category, target_category, amount, *, actor=None):
    from user.models import PragmadinarWallet

    if source_category not in WALLET_CATEGORIES or target_category not in WALLET_CATEGORIES:
        raise ValidationError({"error": "Portefeuille invalide."})
    if source_category == target_category:
        raise ValidationError({"error": "Choisissez deux portefeuilles différents."})
    try:
        amount = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValidationError({"error": "Montant de transfert invalide."}) from exc
    if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise ValidationError({"error": "Le montant doit être positif, avec au plus deux décimales."})

    for category in (source_category, target_category):
        get_or_create_wallet(supplier, category=category)
    locked = {
        wallet.category: wallet
        for wallet in PragmadinarWallet.objects.select_for_update()
        .filter(supplier=supplier, category__in=[source_category, target_category])
        .order_by("pk")
    }
    source = locked[source_category]
    destination = locked[target_category]
    if amount > source.available_balance:
        raise InsufficientPragmadinar(amount, source.available_balance)
    _record_wallet_transfer(source, destination, amount, actor=actor)
    return wallet_payload(supplier)


@transaction.atomic
def reallocate_wallet_distribution(supplier, allocations, *, actor=None):
    from user.models import PragmadinarWallet

    try:
        raw_targets = {
            category: Decimal(str(allocations[category]))
            for category in WALLET_CATEGORIES
        }
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"error": "Indiquez les trois montants à répartir."}) from exc
    if any(value != value.quantize(Decimal("0.01")) for value in raw_targets.values()):
        raise ValidationError({"error": "Les montants sont limités à deux décimales."})
    targets = {
        category: value.quantize(Decimal("0.01"))
        for category, value in raw_targets.items()
    }
    if any(amount < 0 for amount in targets.values()):
        raise ValidationError({"error": "Les montants ne peuvent pas être négatifs."})

    for category in WALLET_CATEGORIES:
        get_or_create_wallet(supplier, category=category)
    wallets = {
        wallet.category: wallet
        for wallet in PragmadinarWallet.objects.select_for_update()
        .filter(supplier=supplier, category__in=WALLET_CATEGORIES)
        .order_by("pk")
    }
    available = {category: wallet.available_balance for category, wallet in wallets.items()}
    if sum(targets.values(), Decimal("0")) != sum(available.values(), Decimal("0")):
        raise ValidationError(
            {"error": "La répartition doit conserver le total disponible des trois portefeuilles."}
        )

    donors = [[category, available[category] - targets[category]] for category in WALLET_CATEGORIES]
    recipients = [[category, targets[category] - available[category]] for category in WALLET_CATEGORIES]
    for source_category, surplus in donors:
        if surplus <= 0:
            continue
        for target in recipients:
            target_category, deficit = target
            if deficit <= 0:
                continue
            amount = min(surplus, deficit)
            _record_wallet_transfer(
                wallets[source_category], wallets[target_category], amount, actor=actor
            )
            surplus -= amount
            target[1] -= amount
            if surplus == 0:
                break
    return wallet_payload(supplier)


PRAGMADINAR_AMOUNT_QUANT = Decimal("0.01")


def format_pragmadinar_amount(value) -> str:
    """Always two decimals (0.01 Pragmadinar precision) for API and UI."""
    if value is None:
        return "0.00"
    amount = Decimal(str(value)).quantize(PRAGMADINAR_AMOUNT_QUANT)
    return f"{amount:.2f}"


def wallet_payload(supplier, category=None):
    """Balance summary with the DA equivalent, for the supplier UI."""
    from user.models import PragmadinarWallet

    if category:
        wallets = [get_or_create_wallet(supplier, category=category)]
    else:
        for wallet_category in ("visibility", "analytics", "promotion"):
            get_or_create_wallet(supplier, category=wallet_category)
        wallets = list(PragmadinarWallet.objects.filter(supplier=supplier))

    balance = sum((wallet.balance for wallet in wallets), Decimal("0"))
    held = sum((wallet.held_total for wallet in wallets), Decimal("0"))
    available = balance - held
    granted_total = sum((wallet.granted_total for wallet in wallets), Decimal("0"))
    purchased_total = sum((wallet.purchased_total for wallet in wallets), Decimal("0"))
    spent_total = sum((wallet.spent_total for wallet in wallets), Decimal("0"))
    category_payloads = [
        {
            "category": wallet.category,
            "balance": format_pragmadinar_amount(wallet.balance),
            "available": format_pragmadinar_amount(wallet.available_balance),
            "held": format_pragmadinar_amount(wallet.held_total),
            "grantedTotal": format_pragmadinar_amount(wallet.granted_total),
            "purchasedTotal": format_pragmadinar_amount(wallet.purchased_total),
            "spentTotal": format_pragmadinar_amount(wallet.spent_total),
        }
        for wallet in wallets
    ]
    return {
        "balance": format_pragmadinar_amount(balance),
        "available": format_pragmadinar_amount(available),
        "held": format_pragmadinar_amount(held),
        "grantedTotal": format_pragmadinar_amount(granted_total),
        "purchasedTotal": format_pragmadinar_amount(purchased_total),
        "spentTotal": format_pragmadinar_amount(spent_total),
        "rateDzd": str(pragmadinar_rate_dzd()),
        "balanceDzd": str(pragmadinar_to_dzd(balance)),
        "availableDzd": str(pragmadinar_to_dzd(available)),
        "currency": "Pragmadinar",
        "wallets": category_payloads,
    }


def opportunity_unlock_cost():
    return get_service(OPPORTUNITY_SERVICE_CODE).cost_pragmadinar


def seed_pragmadinar_catalog():
    from user.models import PragmadinarService, PaknomikPlan

    for plan in PLANS:
        PaknomikPlan.objects.update_or_create(code=plan["code"], defaults=plan)
    for row in CATALOG_SERVICES:
        PragmadinarService.objects.update_or_create(code=row["code"], defaults=row)
    PragmadinarService.objects.filter(code__in=RETIRED_SERVICE_CODES).update(is_active=False)
    PragmadinarService.objects.filter(code__in=UNBILLED_SERVICE_CODES).update(
        cost_pragmadinar=Decimal("0"),
    )


def _lock_supplier_wallets(supplier):
    from user.models import PragmadinarWallet

    for category in WALLET_CATEGORIES:
        get_or_create_wallet(supplier, category=category)
    return list(PragmadinarWallet.objects.select_for_update().filter(supplier=supplier))


def _write_adjustment(wallet, *, amount, before, after, reason, actor, idempotency_key, ref_type):
    from user.models import PragmadinarLedger

    return PragmadinarLedger.objects.create(
        wallet=wallet,
        service=None,
        entry_type=PragmadinarLedger.EntryType.ADJUSTMENT,
        status=PragmadinarLedger.Status.POSTED,
        amount=amount,
        balance_before=before,
        balance_after=after,
        rate_dzd=pragmadinar_rate_dzd(),
        amount_dzd=pragmadinar_to_dzd(abs(amount)),
        reference=_new_reference("ADJ"),
        idempotency_key=idempotency_key,
        reason=reason[:255],
        ref_type=ref_type,
        refundable=False,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


def _reduce_total(wallets, remaining):
    """Remove `remaining` from wallet balances. Caps holds when free balance is not enough."""
    ordered = sorted(wallets, key=lambda row: 0 if row.category == "visibility" else 1)
    for wallet in ordered:
        if remaining <= 0:
            break
        free = wallet.balance - wallet.held_total
        if free <= 0:
            continue
        cut = min(free, remaining)
        wallet.balance -= cut
        wallet.save(update_fields=["balance", "updated_at"])
        remaining -= cut
    for wallet in ordered:
        if remaining <= 0:
            break
        if wallet.balance <= 0:
            continue
        cut = min(wallet.balance, remaining)
        wallet.balance -= cut
        if wallet.held_total > wallet.balance:
            wallet.held_total = wallet.balance
        wallet.save(update_fields=["balance", "held_total", "updated_at"])
        remaining -= cut
    return remaining


@transaction.atomic
def reset_supplier_balance(supplier, *, reason, actor=None):
    """Set this supplier's total balance to 200 exactly once."""
    from user.models import PragmadinarLedger

    key = f"{BALANCE_RESET_PREFIX}{supplier.pk}"
    if PragmadinarLedger.objects.filter(
        wallet__supplier=supplier, idempotency_key=key
    ).exists():
        return None
    wallets = _lock_supplier_wallets(supplier)
    before = sum((row.balance for row in wallets), Decimal("0"))
    delta = WELCOME_BALANCE - before
    visibility = next(row for row in wallets if row.category == "visibility")
    if delta > 0:
        visibility.balance += delta
        visibility.save(update_fields=["balance", "updated_at"])
    elif delta < 0:
        _reduce_total(wallets, -delta)
    after = _supplier_balance_total(supplier)
    visibility.refresh_from_db()
    return _write_adjustment(
        visibility,
        amount=after - before,
        before=before,
        after=after,
        reason=reason,
        actor=actor,
        idempotency_key=key,
        ref_type="balance_reset",
    )


def reset_all_supplier_balances(*, apply=False, reason=""):
    """Dry-run unless apply is true. Never called on startup."""
    from user.models import PragmadinarLedger, Supplier

    reason = reason or "Remise administrative du solde à 200 Pragmadinar"
    report = []
    for supplier in Supplier.objects.all().order_by("pk"):
        key = f"{BALANCE_RESET_PREFIX}{supplier.pk}"
        already = PragmadinarLedger.objects.filter(
            wallet__supplier=supplier, idempotency_key=key
        ).exists()
        before = _supplier_balance_total(supplier)
        row = {
            "supplier_id": supplier.pk,
            "company_name": supplier.company_name,
            "previous_balance": before,
            "new_balance": before if already else WELCOME_BALANCE,
            "already_reset": already,
        }
        if apply and not already:
            reset_supplier_balance(supplier, reason=reason)
            row["new_balance"] = _supplier_balance_total(supplier)
        report.append(row)
    return report


@transaction.atomic
def adjust_supplier_balance(supplier, *, mode, amount, reason, actor):
    """Add, subtract, or set the supplier total. One adjustment ledger row."""
    from user.models import PragmadinarLedger

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"error": "Le motif est obligatoire.", "code": "reason_required"})
    if mode not in {"add", "subtract", "set"}:
        raise ValidationError({"error": "Mode d'ajustement inconnu.", "code": "invalid_mode"})
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError({"error": "Montant invalide.", "code": "invalid_amount"}) from exc
    if amount < 0 or (mode != "set" and amount == 0):
        raise ValidationError({"error": "Le montant doit être positif.", "code": "invalid_amount"})

    wallets = _lock_supplier_wallets(supplier)
    before = sum((row.balance for row in wallets), Decimal("0"))
    visibility = next(row for row in wallets if row.category == "visibility")
    if mode == "set":
        delta = amount - before
    elif mode == "add":
        delta = amount
    else:
        free = sum((row.balance - row.held_total for row in wallets), Decimal("0"))
        if amount > free:
            raise ValidationError(
                {
                    "error": "Le retrait dépasse le solde disponible.",
                    "code": "insufficient_pragmadinars",
                }
            )
        delta = -amount

    if delta > 0:
        visibility.balance += delta
        visibility.save(update_fields=["balance", "updated_at"])
    elif delta < 0:
        _reduce_total(wallets, -delta)
    after = _supplier_balance_total(supplier)
    visibility.refresh_from_db()
    entry = _write_adjustment(
        visibility,
        amount=after - before,
        before=before,
        after=after,
        reason=reason,
        actor=actor,
        idempotency_key="",
        ref_type="admin_adjustment",
    )
    return entry
