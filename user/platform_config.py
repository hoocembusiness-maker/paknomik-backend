"""
Platform-wide settings stored in the database.

Examples: Pragmadinar price, feature toggles, promo dates. Admins change values
in admin; code reads them with `get_setting` instead of hardcoding.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

#: key -> (default value as string, human label)
DEFAULT_SETTINGS = {
    "pragmadinar_rate_dzd": ("200", "Valeur d'un Pragmadinar en dinars algériens"),
    "supplier_welcome_pragmadinar": (
        "100",
        "Crédit Pragmadinar offert à chaque fournisseur (bienvenue / mise en service tarifaire)",
    ),
    "pragmadinar_refunds_enabled": (
        "false",
        "Autoriser les remboursements commerciaux (technique toujours possible)",
    ),
    "pragmadinar_recharge_mode": (
        "sandbox",
        "Mode de recharge : sandbox (validation admin) ou provider",
    ),
    "public_quote_unlock_enabled": (
        "false",
        "Accès payant d'un fournisseur non sélectionné à une demande publique (à valider)",
    ),
    "pak_dating_enabled": (
        "false",
        "Pak-dating avec consentement acheteur (Paknomik 2.0)",
    ),
    "pak_dating_consent_ttl_hours": (
        "72",
        "Durée de validité d'une demande de consentement Pak-dating (heures)",
    ),
    "benchmark_min_suppliers": (
        "5",
        "Nombre minimum de fournisseurs pour publier un benchmark anonymisé",
    ),
    "benchmark_category_show_pragmadinar": (
        "1",
        "Benchmark catégorie : afficher la carte de solde Pragmadinar (1) ou la masquer (0)",
    ),
    "market_session_gap_minutes": (
        "30",
        "Market Dashboards : inactivité (minutes) qui clôt une session de navigation",
    ),
    "market_min_population": (
        "3",
        "Market Dashboards : effectif minimum (sessions) avant d'afficher un mot-clé ou une wilaya",
    ),
    "market_min_suppliers": (
        "1",
        "Market Dashboards : fournisseurs distincts minimum avant de publier un chiffre",
    ),
    "dashboard_market_min_suppliers": (
        "3",
        "Tableau de bord fournisseur : fournisseurs distincts minimum avant d'afficher une part de marché",
    ),
    "market_low_results_threshold": (
        "2",
        "Market Dashboards : nombre moyen de résultats en dessous duquel une recherche est « faible »",
    ),
    "followup_reminder_hours": (
        "24,48",
        "Délais de relance automatique (heures, séparés par des virgules)",
    ),
    "commission_rate_min_percent": (
        "5",
        "Taux minimum autorisé dans un mandat de commission (%, vide = sans borne)",
    ),
    "commission_rate_max_percent": (
        "8",
        "Taux maximum autorisé dans un mandat de commission (%, vide = sans borne)",
    ),
    "visibility_metering_enabled": (
        "true",
        "Comptage mensuel des apparitions et visites (quotas / consommation Pragmadinar)",
    ),
    "visit_dwell_seconds": (
        "8",
        "Durée minimale sur une fiche (secondes) avant qu'une visite boutique/produit soit comptée",
    ),
    "search_appearance_dedupe_seconds": (
        "0",
        "Anti-doublon apparition en recherche (0 = chaque chargement de résultats compte immédiatement)",
    ),
    "social_facebook_url": ("", "Lien de la page Facebook Paknomik"),
    "social_instagram_url": ("", "Lien du compte Instagram Paknomik"),
    "social_whatsapp_url": ("", "Lien WhatsApp Paknomik (wa.me)"),
}


def visit_dwell_seconds() -> int:
    return max(1, get_int("visit_dwell_seconds", 8))


def search_appearance_dedupe_seconds() -> int:
    return max(0, get_int("search_appearance_dedupe_seconds", 0))


def seed_settings():
    from user.models import PlatformSetting

    for key, (value, label) in DEFAULT_SETTINGS.items():
        PlatformSetting.objects.get_or_create(
            key=key, defaults={"value": value, "label": label}
        )
        PlatformSetting.objects.filter(key=key, label="").update(label=label)


def get_setting(key, default=None):
    from user.models import PlatformSetting

    row = PlatformSetting.objects.filter(key=key).first()
    if row is not None and row.value != "":
        return row.value
    if default is not None:
        return default
    fallback = DEFAULT_SETTINGS.get(key)
    return fallback[0] if fallback else None


def get_bool(key, default=False) -> bool:
    raw = get_setting(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def get_int(key, default=0) -> int:
    try:
        return int(str(get_setting(key)).strip())
    except (TypeError, ValueError):
        return default


def get_decimal(key, default="0") -> Decimal:
    try:
        return Decimal(str(get_setting(key)).strip())
    except Exception:
        return Decimal(str(default))


def get_optional_decimal(key):
    """Decimal setting where an existing empty value means 'unset' (None)."""
    from user.models import PlatformSetting

    row = PlatformSetting.objects.filter(key=key).first()
    if row is None:
        fallback = DEFAULT_SETTINGS.get(key)
        text = fallback[0] if fallback else ""
    else:
        text = row.value
    text = (text or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def get_date(key, default=None):
    """Parse a date setting. An existing empty value means 'unset' and does
    not fall back to DEFAULT_SETTINGS, so a promo can be turned off."""
    from user.models import PlatformSetting

    row = PlatformSetting.objects.filter(key=key).first()
    if row is None:
        fallback = DEFAULT_SETTINGS.get(key)
        text = fallback[0] if fallback else None
    else:
        text = str(row.value).strip()
        if text == "":
            return default
    if not text:
        return default
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return default


def get_int_list(key) -> list[int]:
    raw = get_setting(key) or ""
    values = []
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.append(int(chunk))
        except ValueError:
            continue
    return values


def set_setting(key, value, *, label=""):
    from user.models import PlatformSetting

    row, _ = PlatformSetting.objects.update_or_create(
        key=key, defaults={"value": str(value)}
    )
    if label and not row.label:
        row.label = label
        row.save(update_fields=["label"])
    return row


def pragmadinar_rate_dzd() -> Decimal:
    """1 Pragmadinar = 200 DA by default, configurable."""
    rate = get_decimal("pragmadinar_rate_dzd", "200")
    return rate if rate > 0 else Decimal("200")


def supplier_welcome_pragmadinar() -> Decimal:
    """One-time welcome grant when tarification starts (default 100 Pragmadinar)."""
    amount = get_decimal("supplier_welcome_pragmadinar", "100")
    return amount if amount > 0 else Decimal("0")


def pragmadinar_to_dzd(amount) -> Decimal:
    return (Decimal(str(amount)) * pragmadinar_rate_dzd()).quantize(Decimal("0.01"))


# Historical migrations import this name; remove only after their compatibility window.
supplier_welcome_pakdinar = supplier_welcome_pragmadinar
