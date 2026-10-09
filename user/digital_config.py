"""
Digital presence configuration: OAuth apps, pinned API versions, scopes, metric catalog.

Every credential comes from server-side environment variables. Nothing here is
ever sent to the browser except the `public_*` helpers (labels, availability).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.conf import settings

from user.digital_models import DigitalPlatform

# ── Pinned API versions (one place to bump) ─────────────────────────────────

META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v26.0")
GA4_DATA_API = "https://analyticsdata.googleapis.com/v1beta"
GA4_ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
SEARCH_CONSOLE_API = "https://www.googleapis.com/webmasters/v3"

OAUTH_STATE_MAX_AGE_SECONDS = 600
OAUTH_BINDING_COOKIE = "pk_digital_oauth"
OAUTH_CALLBACK_PREFIX = "/api/integrations/oauth/"

MANUAL_SYNC_COOLDOWN_MINUTES = int(os.getenv("DIGITAL_MANUAL_SYNC_COOLDOWN_MINUTES", "15"))
SCHEDULED_SYNC_MIN_HOURS = 20
BACKFILL_DAYS = 93
INCREMENTAL_DAYS = 10
WINDOWS = (7, 28, 90)
TOP_ROWS = 25
RECENT_CONTENT_LIMIT = 12
UNSUPPORTED_METRIC_RECHECK_DAYS = 7


@dataclass(frozen=True)
class ProviderConfig:
    platform: str
    provider: str  # meta | instagram | google
    label: str
    resource_label: str
    callback_slug: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id_env: str
    client_secret_env: str
    redirect_env: str
    pkce: bool
    source_timezone: str
    freshness_note: str
    extra_auth_params: dict = field(default_factory=dict)

    @property
    def client_id(self) -> str:
        return os.getenv(self.client_id_env, "").strip()

    @property
    def client_secret(self) -> str:
        return os.getenv(self.client_secret_env, "").strip()

    @property
    def redirect_uri(self) -> str:
        return os.getenv(self.redirect_env, "").strip()

    @property
    def callback_path(self) -> str:
        return f"{OAUTH_CALLBACK_PREFIX}{self.callback_slug}/callback/"


_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_PARAMS = {
    "access_type": "offline",
    "prompt": "consent",
    # Each card asks only for its own read-only scope.
    "include_granted_scopes": "false",
}

PROVIDERS: dict[str, ProviderConfig] = {
    DigitalPlatform.META_FACEBOOK: ProviderConfig(
        platform=DigitalPlatform.META_FACEBOOK,
        provider="meta",
        label="Page Facebook",
        resource_label="Page",
        callback_slug="meta",
        authorize_url=f"https://www.facebook.com/{META_GRAPH_API_VERSION}/dialog/oauth",
        token_url=f"https://graph.facebook.com/{META_GRAPH_API_VERSION}/oauth/access_token",
        scopes=("pages_show_list", "pages_read_engagement", "read_insights"),
        client_id_env="META_APP_ID",
        client_secret_env="META_APP_SECRET",
        redirect_env="META_REDIRECT_URI",
        pkce=os.getenv("META_USE_PKCE", "false").lower() == "true",
        source_timezone="America/Los_Angeles",
        freshness_note=(
            "Meta met à jour la plupart des Insights de Page une fois par 24 h "
            "(jours calculés en heure du Pacifique). Disponible uniquement pour les Pages "
            "d'au moins 100 mentions J'aime."
        ),
    ),
    DigitalPlatform.META_INSTAGRAM: ProviderConfig(
        platform=DigitalPlatform.META_INSTAGRAM,
        provider="instagram",
        label="Instagram professionnel",
        resource_label="Compte Instagram",
        callback_slug="instagram",
        authorize_url="https://www.instagram.com/oauth/authorize",
        token_url="https://api.instagram.com/oauth/access_token",
        scopes=("instagram_business_basic", "instagram_business_manage_insights"),
        client_id_env="INSTAGRAM_APP_ID",
        client_secret_env="INSTAGRAM_APP_SECRET",
        redirect_env="INSTAGRAM_REDIRECT_URI",
        pkce=False,
        source_timezone="",
        freshness_note=(
            "Valeurs estimées par Meta, mises à jour avec jusqu'à 48 h de délai. "
            "Comptes Business ou Créateur uniquement ; certaines métriques sont "
            "indisponibles pour les petits comptes."
        ),
        extra_auth_params={"enable_fb_login": "0", "force_authentication": "1"},
    ),
    DigitalPlatform.GOOGLE_ANALYTICS: ProviderConfig(
        platform=DigitalPlatform.GOOGLE_ANALYTICS,
        provider="google",
        label="Google Analytics 4",
        resource_label="Propriété GA4",
        callback_slug="google",
        authorize_url=_GOOGLE_AUTH,
        token_url=_GOOGLE_TOKEN,
        scopes=("https://www.googleapis.com/auth/analytics.readonly",),
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        redirect_env="GOOGLE_REDIRECT_URI",
        pkce=True,
        source_timezone="",
        freshness_note=(
            "Google Analytics traite les données avec 24 à 48 h de délai ; "
            "les jours suivent le fuseau horaire de la propriété."
        ),
        extra_auth_params=_GOOGLE_PARAMS,
    ),
    DigitalPlatform.GOOGLE_SEARCH_CONSOLE: ProviderConfig(
        platform=DigitalPlatform.GOOGLE_SEARCH_CONSOLE,
        provider="google",
        label="Google Search Console",
        resource_label="Propriété Search Console",
        callback_slug="google",
        authorize_url=_GOOGLE_AUTH,
        token_url=_GOOGLE_TOKEN,
        scopes=("https://www.googleapis.com/auth/webmasters.readonly",),
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        redirect_env="GOOGLE_REDIRECT_URI",
        pkce=True,
        source_timezone="America/Los_Angeles",
        freshness_note=(
            "Search Console publie les données finales avec 2 à 3 jours de délai "
            "(jours en heure du Pacifique). Seules les lignes principales renvoyées "
            "par Google sont affichées, pas un export exhaustif."
        ),
        extra_auth_params=_GOOGLE_PARAMS,
    ),
}

#: Days between "today" and the last complete day each source can provide.
DATA_LAG_DAYS = {
    DigitalPlatform.META_FACEBOOK: 1,
    DigitalPlatform.META_INSTAGRAM: 1,
    DigitalPlatform.GOOGLE_ANALYTICS: 1,
    DigitalPlatform.GOOGLE_SEARCH_CONSOLE: 3,
}

# ── Metric catalog ──────────────────────────────────────────────────────────
#
# `aggregation`:
#   "sum"    — additive per day; a window total is the sum of daily values, and
#              only when every day of the window is present.
#   "window" — non-additive (unique users, rates, positions): only a value
#              computed by the source for the exact window is shown.


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    definition: str
    aggregation: str
    chart: bool = False
    provider_metric: str = ""

    @property
    def api_name(self) -> str:
        return self.provider_metric or self.key


METRICS: dict[str, tuple[MetricSpec, ...]] = {
    DigitalPlatform.META_FACEBOOK: (
        MetricSpec(
            "page_media_view",
            "Vues de contenu",
            "count",
            "Nombre de fois où le contenu de la Page a été affiché ou lu (remplace les impressions).",
            "sum",
            chart=True,
        ),
        MetricSpec(
            "page_total_media_view_unique",
            "Spectateurs uniques",
            "count",
            "Nombre estimé de personnes uniques ayant vu le contenu de la Page sur la période.",
            "window",
        ),
        MetricSpec(
            "page_post_engagements",
            "Interactions avec les publications",
            "count",
            "Réactions, commentaires, partages et clics sur les publications de la Page.",
            "sum",
            chart=True,
        ),
        MetricSpec(
            "page_total_actions",
            "Clics sur les actions de la Page",
            "count",
            "Clics sur les coordonnées et le bouton d'appel à l'action de la Page.",
            "sum",
        ),
    ),
    DigitalPlatform.META_INSTAGRAM: (
        MetricSpec(
            "reach",
            "Portée",
            "count",
            "Nombre estimé de comptes uniques ayant vu le contenu au moins une fois.",
            "window",
            chart=True,
        ),
        MetricSpec(
            "views",
            "Vues",
            "count",
            "Nombre de fois où le contenu (reels, publications, stories) a été lu ou affiché.",
            "window",
        ),
        MetricSpec(
            "accounts_engaged",
            "Comptes engagés",
            "count",
            "Nombre estimé de comptes ayant interagi avec le contenu.",
            "window",
        ),
        MetricSpec(
            "total_interactions",
            "Interactions",
            "count",
            "Mentions J'aime, commentaires, enregistrements, partages et réponses.",
            "window",
        ),
        MetricSpec(
            "profile_views",
            "Visites du profil",
            "count",
            "Visites du profil Instagram, si Meta fournit encore cette métrique pour le compte.",
            "window",
        ),
    ),
    DigitalPlatform.GOOGLE_ANALYTICS: (
        MetricSpec(
            "sessions",
            "Sessions",
            "count",
            "Nombre de sessions démarrées sur le site ou l'application (GA4).",
            "window",
            chart=True,
        ),
        MetricSpec(
            "activeUsers",
            "Utilisateurs actifs",
            "count",
            "Utilisateurs distincts ayant eu une session engagée ou un premier lancement.",
            "window",
            chart=True,
        ),
        MetricSpec(
            "screenPageViews",
            "Vues de pages",
            "count",
            "Nombre de pages web et d'écrans d'application vus (répétitions incluses).",
            "window",
        ),
        MetricSpec(
            "engagementRate",
            "Taux d'engagement",
            "ratio",
            "Part des sessions engagées (plus de 10 s, conversion ou 2 pages vues).",
            "window",
        ),
        MetricSpec(
            "keyEvents",
            "Événements clés",
            "count",
            "Nombre d'événements marqués comme clés dans la propriété (affiché seulement s'il en existe).",
            "window",
        ),
    ),
    DigitalPlatform.GOOGLE_SEARCH_CONSOLE: (
        MetricSpec(
            "clicks",
            "Clics",
            "count",
            "Clics depuis les résultats de recherche Google vers le site (recherche Web).",
            "window",
            chart=True,
        ),
        MetricSpec(
            "impressions",
            "Impressions",
            "count",
            "Nombre de fois où une page du site est apparue dans les résultats Google.",
            "window",
            chart=True,
        ),
        MetricSpec(
            "ctr",
            "CTR",
            "ratio",
            "Clics divisés par impressions, calculé par Google.",
            "window",
        ),
        MetricSpec(
            "position",
            "Position moyenne",
            "position",
            "Position moyenne la plus haute du site dans les résultats (1 = premier).",
            "window",
        ),
    ),
}

TOP_DIMENSIONS = {
    DigitalPlatform.GOOGLE_SEARCH_CONSOLE: (
        ("query", "Principales requêtes"),
        ("page", "Principales pages"),
        ("country", "Pays"),
        ("device", "Appareils"),
    ),
}


def metric_spec(platform: str, key: str) -> MetricSpec | None:
    for spec in METRICS.get(platform, ()):
        if spec.key == key:
            return spec
    return None


# ── Redirect URI validation ─────────────────────────────────────────────────

_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def _allowed_redirect_hosts() -> set[str]:
    hosts = set()
    for url in (
        os.getenv("APP_BASE_URL", ""),
        getattr(settings, "BACKEND_URL", ""),
        getattr(settings, "FRONTEND_URL", ""),
    ):
        if url:
            parsed = urlparse(url)
            if parsed.hostname:
                hosts.add(parsed.hostname)
    return hosts


def validate_redirect_uri(config: ProviderConfig) -> tuple[str, str]:
    """Return (uri, "") when the configured callback is acceptable, else ("", reason)."""
    uri = config.redirect_uri
    if not uri:
        return "", f"{config.redirect_env} n'est pas défini."
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return "", f"{config.redirect_env} ne doit contenir ni paramètres ni fragment."
    if parsed.scheme == "http":
        if not (settings.DEBUG and host in _LOCAL_HOSTS):
            return "", f"{config.redirect_env} doit utiliser HTTPS hors développement local."
    elif parsed.scheme != "https":
        return "", f"{config.redirect_env} doit utiliser HTTPS."
    if parsed.path != config.callback_path:
        return "", f"{config.redirect_env} doit se terminer par {config.callback_path}."
    if host not in _allowed_redirect_hosts() and host not in _LOCAL_HOSTS:
        return "", f"{config.redirect_env} pointe vers un domaine non autorisé ({host})."
    if host in _LOCAL_HOSTS and not settings.DEBUG:
        return "", f"{config.redirect_env} ne peut pas viser localhost en production."
    return uri, ""


def configuration_problem(platform: str) -> str:
    """Human-readable reason why a platform cannot be connected, or ""."""
    from user.digital_crypto import encryption_configured

    config = PROVIDERS[platform]
    missing = [
        name
        for name, value in (
            (config.client_id_env, config.client_id),
            (config.client_secret_env, config.client_secret),
        )
        if not value
    ]
    if missing:
        return "Variables manquantes côté serveur : " + ", ".join(missing) + "."
    _uri, problem = validate_redirect_uri(config)
    if problem:
        return problem
    if not encryption_configured():
        return "TOKEN_ENCRYPTION_KEY absente ou invalide : les jetons ne peuvent pas être chiffrés."
    return ""


def frontend_base_url() -> str:
    base = os.getenv("APP_BASE_URL", "") or getattr(settings, "FRONTEND_URL", "")
    return base.rstrip("/")
