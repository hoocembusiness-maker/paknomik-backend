# Présence digitale — configuration et exploitation

Ce module permet à un fournisseur de connecter, via OAuth et **uniquement côté serveur**,
ses propres comptes :

| Source | Flux OAuth | Hôte API | Scopes demandés |
|---|---|---|---|
| Pages Facebook | Facebook Login for Business | `graph.facebook.com/{META_GRAPH_API_VERSION}` | `pages_show_list`, `pages_read_engagement`, `read_insights` |
| Instagram professionnel | Instagram API with Instagram Login | `graph.instagram.com/{META_GRAPH_API_VERSION}` | `instagram_business_basic`, `instagram_business_manage_insights` |
| Google Analytics 4 | Google OAuth 2.0 (code + PKCE, offline) | `analyticsdata.googleapis.com/v1beta`, `analyticsadmin.googleapis.com/v1beta` | `https://www.googleapis.com/auth/analytics.readonly` |
| Search Console | Google OAuth 2.0 (code + PKCE, offline) | `www.googleapis.com/webmasters/v3` | `https://www.googleapis.com/auth/webmasters.readonly` |

Aucune permission de publication, de messagerie ou de publicité n’est demandée
(`ads_read` exclu du MVP). Universal Analytics n’est pas pris en charge. Le module ne
scrape aucune page publique.

## 1. Variables d’environnement

Voir `.env.example`. Toutes les valeurs sont lues côté serveur ; ne jamais les coller
dans le dépôt, le chat ou le frontend.

Générer la clé de chiffrement des jetons :

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Rotation : mettre la nouvelle clé en premier, conserver l’ancienne après une virgule,
relancer une synchronisation complète, puis retirer l’ancienne clé.

Sans `TOKEN_ENCRYPTION_KEY` ou sans identifiants d’application, les cartes affichent
« configuration incomplète » et aucun flux OAuth n’est lancé.

## 2. URI de redirection à enregistrer

Les callbacks sont servis par le backend Django :

```
{BACKEND}/api/integrations/oauth/meta/callback/
{BACKEND}/api/integrations/oauth/instagram/callback/
{BACKEND}/api/integrations/oauth/google/callback/
```

Validation côté serveur : HTTPS obligatoire (HTTP uniquement pour `localhost` en
`DEBUG`), pas de paramètres ni de fragment, chemin exact, hôte égal à `APP_BASE_URL`,
`BACKEND_URL` ou `FRONTEND_URL`.

**Cookie de liaison navigateur** : le bouton « Connecter » dépose un cookie HttpOnly
`pk_digital_oauth` (SameSite=Lax, `Secure` hors DEBUG, chemin `/api/integrations/oauth/`)
vérifié au retour. Le frontend et l’API doivent donc partager le même site (même
domaine enregistrable, ou API derrière le proxy du frontend). En local, Vite
(`localhost:5173`) et Django (`localhost:8000`) partagent les cookies de `localhost`.

## 3. Google Cloud

1. Créer (ou réutiliser) un projet Google Cloud.
2. Activer les API :
   - **Google Analytics Data API**
   - **Google Analytics Admin API** (liste des propriétés GA4)
   - **Google Search Console API**
3. Écran de consentement OAuth : type « Externe », ajouter les deux scopes ci-dessus.
   Ce sont des scopes **sensibles** : la vérification Google est nécessaire avant la mise
   en production (en mode test, seuls les utilisateurs de test déclarés peuvent se connecter).
4. Identifiants → client OAuth « Application Web » ; ajouter l’URI
   `.../api/integrations/oauth/google/callback/`. Le même client que la connexion Google
   existante peut être réutilisé, à condition d’y ajouter cette URI.
5. Renseigner `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.

Le flux utilise `access_type=offline` et `prompt=consent` pour obtenir un refresh token ;
un `invalid_grant` (révocation, mot de passe changé, application en mode test > 7 jours)
place la connexion en « À reconnecter ».

## 4. Meta for Developers

### Facebook Pages
1. Créer une application de type « Business ».
2. Ajouter **Facebook Login for Business** ; déclarer l’URI
   `.../api/integrations/oauth/meta/callback/` dans « Valid OAuth Redirect URIs » et
   activer « Strict mode for redirect URIs ».
3. App Review : demander l’**Advanced Access** pour `pages_show_list`,
   `pages_read_engagement`, `read_insights` (vidéo de démonstration + justification).
   Sans revue, seuls les comptes ayant un rôle sur l’application peuvent se connecter.
4. Vérification Business (Business Verification) exigée pour l’Advanced Access.
5. Renseigner `META_APP_ID`, `META_APP_SECRET`, `META_REDIRECT_URI`. Laisser
   `META_USE_PKCE=false` sauf si PKCE est activé pour l’application.

### Instagram
1. Ajouter le produit **Instagram API with Instagram Login** (identifiants distincts :
   `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`).
2. Déclarer `.../api/integrations/oauth/instagram/callback/`.
3. App Review : `instagram_business_basic`, `instagram_business_manage_insights`.
4. Le compte connecté doit être **Professionnel (Business ou Créateur)** ; un compte
   personnel est refusé avec un message explicite.

Les jetons, hôtes et scopes Facebook et Instagram ne sont jamais mélangés.

### Version Graph API
`META_GRAPH_API_VERSION` (par défaut `v26.0`) est centralisée dans
`user/digital_config.py`. À chaque montée de version : relire le changelog Meta, mettre à
jour le catalogue `METRICS` et relancer les tests. Les métriques refusées par l’API
(erreur 100) sont mémorisées par connexion et réessayées tous les 7 jours.

## 5. Synchronisation planifiée

Aucun appel API n’est fait à l’affichage d’une page. Planifier une fois par jour :

```bash
python manage.py sync_digital_presence           # connexions non synchronisées depuis 20 h
python manage.py sync_digital_presence --force   # toutes
python manage.py sync_digital_presence --connection 12
```

- Linux (cron) : `15 5 * * * cd /srv/paknomik-backend && .venv/bin/python manage.py sync_digital_presence >> logs/digital_sync.log 2>&1`
- Windows (Planificateur de tâches) : action `python.exe`, arguments
  `manage.py sync_digital_presence`, dossier de démarrage = racine du backend.

Première synchronisation : 93 jours d’historique ; ensuite 10 jours glissants (corrige
les révisions tardives). Délai d’indisponibilité : Meta et GA4 = J-1, Search Console = J-3
(données « final » uniquement). Resynchronisation manuelle : 1 fois / 15 min par connexion.

## 6. Tests en développement

```bash
python manage.py migrate
python manage.py test user.tests_digital_presence
```

Les tests utilisent un transport HTTP factice : aucun appel réseau, aucun secret réel.

Données de démonstration (DEBUG uniquement, marquées `is_demo` et affichées avec le
badge « Données simulées », jamais mélangées aux données réelles) :

```bash
python manage.py seed_digital_presence_demo --supplier 1
python manage.py seed_digital_presence_demo --supplier 1 --clear
```

Test OAuth réel en local : renseigner les variables, ajouter votre compte comme
testeur (Google) ou rôle de l’application (Meta), ouvrir
`http://localhost:5173/supplier/presence-digitale` et cliquer « Connecter ».

## 7. Droits d’accès

- Fournisseur : uniquement ses propres connexions (le fournisseur est déduit du compte
  connecté ; tout `supplierId` envoyé par le navigateur est ignoré).
- `ULTRA_ADMIN` (ou superutilisateur) : vue globale `/admin/presence-digitale`, sans
  jamais voir de jeton. Les autres administrateurs n’y ont pas accès.
- Déconnexion : révocation côté fournisseur (Google `/revoke`, Meta
  `DELETE /me/permissions`) puis suppression des jetons, connexions et mesures.

## 8. Tarification

Aucun débit Pragmadinar n’est effectué (connexion, déconnexion, synchronisation, consultation,
vue admin). Aucun événement facturable « présence digitale » n’existe dans le code ;
toute facturation nécessite une décision produit préalable.
