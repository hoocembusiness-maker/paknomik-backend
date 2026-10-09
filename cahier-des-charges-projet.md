# Cahier des charges technique et fonctionnel — Paknomik

> **Document généré à partir du code existant** (backend Django + frontend React + templates Django).  
> **Date de référence :** août 2026  
> **Règle :** aucune fonctionnalité inventée ; les éléments non confirmés dans le code sont marqués **« à confirmer »**.

---

## 1. Objectif général de l’application

**Paknomik** est une plateforme B2B de mise en relation autour de l’**emballage industriel et commercial** (packaging marketplace).

D’après le code, l’application permet :

- aux **acheteurs / clients** de parcourir des **fournisseurs** et des **produits** d’emballage, de rechercher, comparer, contacter des fournisseurs et publier des **appels d’offres** ;
- aux **fournisseurs** de présenter leur entreprise, gérer un catalogue produit, recevoir des demandes (RFQ) et des notifications d’appels d’offres ;
- à la plateforme de s’appuyer sur une **base de connaissances catalogue** (catégories, sous-catégories, attributs techniques, matériaux, mots-clés de recherche) importée depuis des fichiers Excel/CSV.

Le projet comporte **deux interfaces utilisateur en parallèle** :

1. **Frontend React** (`paknomik frontend`) — interface principale moderne consommant l’API REST `/api/`.
2. **Interface Django templates** (`paknomik backend/templates/`) — parcours serveur classique (dashboards, CRUD produits, recherche HTML).

Les deux partagent le **même backend Django** et la **même base SQLite** (développement).

---

## 2. Utilisateurs et rôles

### 2.1 Modèle utilisateur (`user.User`, extension de `AbstractUser`)

| Rôle métier | Critères dans le code | Capacités observées |
|-------------|----------------------|---------------------|
| **Visiteur non connecté** | — | Accueil, recherche produits/fournisseurs, fiches publiques |
| **Client simple** | `is_verified_client=False` (défaut) | Inscription, profil incomplet |
| **Client vérifié** | `is_verified_client=True` | Accès `contact_supplier` (Django), appels d’offres (React API) |
| **Fournisseur** | `user.supplier_id` renseigné (FK → `Supplier`) | Dashboard fournisseur, gestion produits (Django), dashboard API (React) |
| **Staff / superuser** | `is_staff` / `is_superuser` (Django admin) | Administration Django |

Champs profil client sur `User` : `company_name`, `nature_of_activity`, `annual_purchase_volume`, `wilaya`, `address`, `phone_number`.

### 2.2 Fournisseur (`user.Supplier`)

Entité entreprise distincte de l’utilisateur, liée via `User.supplier`.

Types : `manufacturer` | `distributor`  
Wilayas : liste de 48 choix algériens codés en dur.  
Statuts : `is_active`, `is_featured`.  
Intérêts catalogue : M2M `interested_categories`, `interested_subcategories`.

### 2.3 Rôles implicites API React

Le frontend distingue les utilisateurs via `AuthUser.isSupplier` (booléen renvoyé à l’inscription/connexion), basé sur la présence d’un lien `user.supplier`.

---

## 3. Fonctionnalités disponibles

### 3.1 Catalogue et recherche (public)

- Liste des **catégories** parentes et **sous-catégories** (API + templates).
- Liste / filtre **fournisseurs** (nom, catégorie, pays — filtre pays limité dans l’API).
- Liste / filtre **produits** (texte, catégorie, fournisseur).
- **Recherche textuelle** produits : nom, description, catégorie, sous-catégorie, mots-clés (`SearchTag`) — via `user/product_search.py`.
- **Attributs techniques** par catégorie/sous-catégorie (filtres N1, fiches produit N2).
- **Suggestions de recherche** JSON (produits + fournisseurs) — route Django `/api/search-suggestions/`.
- **Comparaison de produits** (Django) via paramètres `?ids=`.
- Statistiques plateforme (API `/api/stats`) — hook React présent mais **non utilisé par les pages** (à confirmer côté produit).

### 3.2 Authentification

**Django / allauth (`/accounts/`)**  
- Login, signup, logout, OAuth Google / Facebook / LinkedIn (OpenID Connect).  
- Templates custom `templates/account/`.

**API JWT (`/api/auth/`)**  
- Login (`username`, `email` ou `login` + `password`) → token JWT 7 jours.  
- Register (email, password, name, company, isSupplier).  
- Google OAuth : redirect backend → callback → redirect frontend avec `token` et `user` en query string.

**Réinitialisation mot de passe Django (`/auth/`)**  
- Flux natif Django (`django.contrib.auth.urls`) avec templates `templates/registration/`.  
- **Non branché dans le frontend React** (écran « mot de passe oublié » = UI simulée sans appel API — voir section 17).

### 3.3 Espace client

- Inscription et **complétion de profil** (`VerifyClientForm` → `is_verified_client=True`).
- Dashboard client (template Django).
- **Appel d’offre** (`TenderCall`) : catégorie, sous-catégorie, quantité, description, type simple/détaillé, attributs techniques optionnels.
- Notification email aux fournisseurs concernés (synchrone, `send_mail`).
- **RFQ** (demande de devis) vers un fournisseur via API `POST /api/rfq/`.
- Contact fournisseur (Django) : redirection WhatsApp ou `mailto:` si client vérifié.

### 3.4 Espace fournisseur

**Django templates**  
- Devenir fournisseur (`SupplierRegistrationForm`).  
- Dashboard, liste produits, ajout/édition produit (`ProductForm` avec catégorie, sous-catégorie, matériau, search_tags, prix, MOQ, délai).  
- Upload images produit via inline admin / **à confirmer** pour le formulaire web fournisseur (ProductForm n’inclut pas d’upload image direct — images via `ProductImage` admin ou non exposé au formulaire).

**React**  
- Dashboard fournisseur (`/supplier/dashboard`) — métriques **partiellement synthétiques** (voir section 17).  
- Pages placeholder : produits, commandes, contacts, analyse marché.

### 3.5 Administration

- Django Admin pour User, Supplier, Category, TechnicalAttribute, Product, etc.
- Commandes d’import catalogue / fournisseurs / mots-clés (voir section 11).

### 3.6 Newsletter

- API `POST /api/newsletter/` — enregistrement email.  
- **Non consommé par le frontend React actuel** (hook défini, page absente).

---

## 4. Parcours utilisateur détaillés

### 4.1 Visiteur → recherche produit (React)

1. Arrivée sur `/` (Home).
2. Saisie recherche ou navigation catégories (carousel).
3. Redirection `/search?q=...&tab=products|suppliers`.
4. Consultation résultats API `/api/products/` ou `/api/suppliers/`.
5. Clic fiche fournisseur `/suppliers/:id`.

### 4.2 Inscription client (React)

1. `/login?mode=register`.
2. `POST /api/auth/register/`.
3. Token + user stockés dans `localStorage`.
4. Redirection `/` (ou `redirect` query param).

### 4.3 Inscription / connexion fournisseur (React)

1. `/login?mode=supplier-register` ou register avec `isSupplier: true`.
2. Création `User` + `Supplier` côté API.
3. Redirection post-login → `/supplier/dashboard`.

### 4.4 Appel d’offre client vérifié (React)

1. `/appel-offre` (redirection login si pas de token).
2. Sélection catégorie → sous-catégories → attributs (`/api/category-attributes/`).
3. `POST /api/tender-calls/` (JWT requis).
4. Backend crée `TenderCall`, notifie fournisseurs par email, retourne `notifiedSupplierCount`.

### 4.5 RFQ sur fiche fournisseur (React)

1. `/suppliers/:id?tab=contact`.
2. Formulaire → `POST /api/rfq/` (supplierId, name, company, email, message).

### 4.6 Parcours Django template (parallèle)

1. `/signup/` → création compte → `/complete-profile/`.
2. `/become-supplier/` → profil fournisseur.
3. `/supplier-dashboard/`, `/add-product/`, `/search/`, etc.

**Conflit URL :** `name='signup'` défini à la fois dans `home.urls` et `user.urls` ; `home.urls` est inclus en premier → `reverse('signup')` pointe vers la vue composant home, pas `user.signup_view`. **À confirmer** en production.

### 4.7 OAuth Google (React)

1. Clic Google → `{BACKEND_URL}/api/auth/google/?redirect=...`.
2. Allauth Google → callback `/api/auth/google/callback/`.
3. Redirect `{FRONTEND_URL}/login?token=...&user=...`.

---

## 5. Pages et écrans de l’application

### 5.1 Frontend React (`paknomik frontend/src/pages/`)

| Route | Composant | Statut |
|-------|-----------|--------|
| `/` | Home | Fonctionnel |
| `/search` | Search | Fonctionnel |
| `/suppliers/:id` | SupplierProfile | Fonctionnel |
| `/login` | Login (modes login/register/forgot/supplier) | Fonctionnel (sauf forgot password réel) |
| `/appel-offre` | AppelOffre | Fonctionnel |
| `/supplier/dashboard` | SupplierDashboard | Fonctionnel (données partiellement simulées) |
| `/supplier/products` | SupplierPlaceholder | **Placeholder** |
| `/supplier/orders` | SupplierPlaceholder | **Placeholder** |
| `/supplier/contacts` | SupplierPlaceholder | **Placeholder** |
| `/supplier/market` | SupplierPlaceholder | **Placeholder** |
| `*` | not-found | 404 |

### 5.2 Django templates (routes actives)

| Template | Route(s) |
|----------|----------|
| `home.html` | `/` |
| `user/signup.html` | `/signup/` (user) — conflit possible avec home |
| `user/complete_profile.html` | `/complete-profile/` |
| `user/client_dashboard.html` | `/client-dashboard/` |
| `user/supplier_dashboard.html` | `/supplier-dashboard/` |
| `user/supplier_products.html` | `/supplier-products/` |
| `user/add_product.html`, `edit_product.html` | `/add-product/`, `/edit-product/<id>/` |
| `user/search_results.html` | `/search/` |
| `user/suppliers_list.html` | `/suppliers/` |
| `user/supplier_detail.html` | `/supplier/<id>/` |
| `user/product_detail.html` | `/product/<id>/` |
| `user/compare_products.html` | `/compare/` |
| `account/login.html`, `signup.html` | `/accounts/login/`, etc. |
| `registration/password_reset_*.html` | `/auth/password_reset/`, etc. |

### 5.3 Templates orphelins (présents sur disque, **sans vue Python référencée**)

`client_inquiries.html`, `inquiry_detail.html`, `supplier_inquiries.html`, `media_detail.html`, `media_list.html`, `edit_profile_supplier.html`, `supplier_contact.html`, `contact_supplier.html` — **écrans prévus mais non branchés**.

---

## 6. Routes frontend et endpoints API

### 6.1 Proxy dev React (Vite)

- `/api/*` → `VITE_DJANGO_URL` (défaut `http://localhost:8000`)
- `/media/*` → même backend

### 6.2 API REST (`/api/`)

| Méthode | Endpoint | Auth | Description |
|---------|----------|------|-------------|
| GET | `/api/stats` | Non | Compteurs plateforme |
| GET | `/api/categories/` | Non | Catégories niveau 1 |
| GET | `/api/subcategories/?parent=` | Non | Sous-catégories |
| GET | `/api/category-attributes/?category=&subcategory=` | Non | Attributs techniques |
| GET | `/api/suppliers/` | Non | Liste fournisseurs |
| GET | `/api/suppliers/<pk>/` | Non | Détail fournisseur |
| GET | `/api/products/` | Non | Liste produits |
| POST | `/api/auth/login/` | Non | JWT login |
| POST | `/api/auth/register/` | Non | Inscription |
| GET | `/api/auth/google/` | Non | Début OAuth Google |
| GET | `/api/auth/google/callback/` | Non | Callback OAuth |
| POST | `/api/rfq/` | Non | Demande de devis |
| POST | `/api/tender-calls/` | **JWT** | Appel d’offre |
| POST | `/api/newsletter/` | Non | Newsletter |
| GET | `/api/supplier/dashboard/?year=&month=` | **JWT** + supplier | Dashboard fournisseur |

### 6.3 Routes Django hors API (sélection)

| Préfixe | Usage |
|---------|-------|
| `/accounts/` | django-allauth |
| `/auth/` | Reset password Django natif |
| `/admin/` | Django Admin |
| `/api/top-suppliers/`, `/api/search-suggestions/` | JSON interne templates |

### 6.4 Code mort API

- `LoginView_email` dans `user/api/views.py` — **non enregistré** dans `urls.py`.

---

## 7. Modèles de données, champs et relations

### 7.1 Schéma relationnel simplifié

```
User ──FK──> Supplier
User ──1:N──> TenderCall

Supplier ──1:N──> Product, Service, RFQRequest
Supplier ──M2M──> Category (interested_categories, interested_subcategories)

Category ──self FK parent──> children (level 1 / 2)
Category ──1:N──> Product (category, subcategory)
Category ──M2M──> TechnicalAttribute (through CategoryAttribute)

Product ──FK──> Supplier, Category, subcategory Category, Material
Product ──M2M──> SearchTag
Product ──1:N──> ProductImage, ProductAttributeValue

TenderCall ──FK──> User, Category, subcategory
TenderCall ──1:N──> TenderCallAttributeValue, TenderCallNotification

TechnicalAttribute ──FK──> Unit (optionnel)
CategoryAttribute ──1:N──> AttributeOption
```

### 7.2 Modèles principaux (marketplace)

**Product**  
`name`, `name_ar`, `description`, `price`, `min_order_quantity`, `lead_time_days`, `is_active`, `is_featured`  
Méthode : `get_display_image()` — image produit, sinon sous-catégorie, sinon catégorie.

**SearchTag**  
`tag` (unique), `tag_fr`, `tag_ar`, `tag_group`, `industry_name`, `sub_category`, `search_weight`, `synonyms`  
Import : `paknomik_mots_cles_produits_exhaustif.csv`.

**TenderCall**  
`tender_type`: `simple` | `detailed` ; notification fournisseurs par sous-catégorie d’intérêt ou produits existants.

### 7.3 Modèles base de connaissances (référence)

Category, TechnicalAttribute (level 1/2), CategoryAttribute, AttributeOption, Unit, Industry, Material, Lexicon, SearchTag, ProductPackagingMapping, PackagingComponent.

Catalogue v4 importé via `import_catalog_v4` (15 catégories, sous-catégories, attributs).  
Segments commerciaux / matrice catégorie-segment du fichier v4 : **non modélisés** (à confirmer roadmap).

### 7.4 Modèle sans usage applicatif

**Service** — défini, aucune vue/API ne l’expose.

---

## 8. Authentification et autorisations

### 8.1 Backends

- `ModelBackend` (Django)
- `allauth.account.auth_backends.AuthenticationBackend`

### 8.2 API

- **JWT** (`rest_framework_simplejwt`) — header `Authorization: Bearer <token>`, durée 7 jours.
- Pas de `SessionAuthentication` dans les defaults REST (évite CSRF sur login API).
- Permission par défaut : `AllowAny` ; exceptions : `TenderCallCreateView`, `SupplierDashboardView` → `IsAuthenticated`.

### 8.3 Décorateurs Django (templates)

| Décorateur | Règle |
|------------|-------|
| `@login_required` | Utilisateur connecté |
| `@verified_client_required` | + `is_verified_client=True` |
| `@supplier_required` | + `user.supplier_id` |

### 8.4 React

- Protection **côté client uniquement** (localStorage, redirects) — pas de middleware frontend global.
- Pas de refresh token ni expiration gérée côté client.

### 8.5 CORS / CSRF

- `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` — noms lus depuis l’environnement.
- `CORS_ALLOW_CREDENTIALS = True`.

---

## 9. Fonctionnement du frontend React

### 9.1 Stack

React 18, TypeScript, Vite 5 (port 5173), Wouter, TanStack Query 5, Tailwind CSS 4, Radix/shadcn UI, react-hook-form + zod, framer-motion, sonner.

### 9.2 Structure clé

- `src/App.tsx` — routing
- `src/lib/api.ts` — `apiFetch`, hooks React Query
- `src/lib/auth.ts` — localStorage, redirects post-login
- `src/components/` — UI (Navbar, layouts fournisseur, etc.)

### 9.3 Flux de données

1. Pages appellent hooks (`useGetCategories`, `useGetProducts`, etc.).
2. `apiFetch` ajoute Bearer token si présent.
3. Cache et refetch via React Query.

### 9.4 Auth stockée

```text
localStorage.token   → JWT string
localStorage.user    → JSON { id, email, name, company, isSupplier }
```

### 9.5 Limitations observées

- Mot de passe oublié : **UI factice** (pas d’appel `/auth/password_reset/`).
- Pages fournisseur produits/commandes/contacts/marché : placeholders.
- Pas de gestion produit côté React (CRUD via Django templates uniquement).

---

## 10. Fonctionnement du backend Django

### 10.1 Stack

Django **5.2.x**, DRF, django-allauth, django-cors-headers, SimpleJWT, Pillow, openpyxl, SQLite.

### 10.2 Apps

`home`, `user`, `accounts` (vide), + contrib + allauth + browser_reload.

### 10.3 Organisation `user`

| Module | Rôle |
|--------|------|
| `models.py` | 20 modèles |
| `views.py` | Vues templates + JSON léger |
| `api/views.py` | API REST |
| `api/serializers.py` | Sérialisation camelCase |
| `forms.py` | Formulaires HTML |
| `permissions.py` | Décorateurs rôle |
| `product_search.py` | Q objects recherche |
| `admin.py` | Admin riche |
| `management/commands/` | Imports données |

### 10.4 Context processor

`home.context_processors.navigation_categories` — injecte toutes les `Category` dans les templates.

### 10.5 Sérializers — champs calculés / statiques

- `SupplierListSerializer` : `rating=4.5`, `reviewCount=0` — **valeurs fixes**, non issues de la BDD.
- `certifications` : liste vide.
- `flag` : 🇩🇿 codé en dur pour l’Algérie.

---

## 11. Traitements en arrière-plan

**Aucun worker asynchrone** (pas de Celery, cron, RQ, Huey dans le code ou `requirements.txt`).

Traitements synchrones à la requête :

- Envoi email appel d’offre (`send_mail`, `fail_silently=True`).
- Imports catalogue/fournisseurs via commandes management (manuel).

---

## 12. Système de journalisation des actions utilisateurs

**Absent.**  
Pas de configuration `LOGGING` dans `settings.py`.  
Pas de modèle `AuditLog`, `Event` actif (**à confirmer** : fichier `user/search_multi.py` référence des modèles `Event` inexistants — code mort / legacy).

---

## 13. Notifications, emails et intégrations externes

### 13.1 Email

- Backend : `EMAIL_BACKEND` (env, défaut console).
- `DEFAULT_FROM_EMAIL` (env).
- Usage : notification fournisseurs lors création `TenderCall`.
- Reset password : templates Django, lien dans email console (dev).

### 13.2 OAuth / social login

- **Google** — configuré (allauth + flux API React).
- **Facebook** — provider allauth + templates.
- **LinkedIn** — OpenID Connect dans `SOCIALACCOUNT_PROVIDERS` (**à confirmer** en conditions réelles).

Variables d’environnement (noms uniquement) :  
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FACEBOOK_CLIENT_ID`, `FACEBOOK_CLIENT_SECRET`, `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`.

### 13.3 WhatsApp

- Contact fournisseur Django : redirect `https://wa.me/{whatsapp_number}`.

### 13.4 Newsletter

- Stockage `NewsletterSubscriber` — API seulement.

---

## 14. Configuration et variables d’environnement

Chargement minimal depuis `.env` à la racine backend (clés lues si absentes de l’environnement).

| Variable | Rôle |
|----------|------|
| `DJANGO_SECRET_KEY` | Clé secrète Django |
| `DJANGO_DEBUG` | Mode debug |
| `DJANGO_ALLOWED_HOSTS` | Hôtes autorisés |
| `EMAIL_BACKEND` | Backend email |
| `DEFAULT_FROM_EMAIL` | Expéditeur |
| `BACKEND_URL` | URL backend (OAuth callback) |
| `FRONTEND_URL` | URL frontend (redirect OAuth, CORS) |
| `CORS_ALLOWED_ORIGINS` | Origines CORS |
| `CSRF_TRUSTED_ORIGINS` | Origines CSRF |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth Google |
| `FACEBOOK_CLIENT_ID` / `FACEBOOK_CLIENT_SECRET` | OAuth Facebook |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | OAuth LinkedIn |
| `VITE_DJANGO_URL` | URL backend (frontend, build Vite) |

**Valeurs réelles : [VALEUR MASQUÉE]** — ne jamais committer `.env`, clés API, mots de passe.

Base de données : SQLite `db.sqlite3` (chemin fixe dans settings, non configurable par env).

---

## 15. Dépendances principales

### Backend (`requirements.txt`)

django, django-allauth, django-browser-reload, djangorestframework, djangorestframework-simplejwt, django-cors-headers, numpy, Pillow, cryptography, openpyxl

*(Pas de versions épinglées.)*

### Frontend (`package.json`)

react, react-dom, wouter, @tanstack/react-query, tailwindcss, vite, typescript, zod, react-hook-form, framer-motion, lucide-react, @radix-ui/* (composants UI)

---

## 16. Instructions d’installation et de lancement

### 16.1 Backend

```bash
cd "paknomik backend"
python -m venv env
env\Scripts\activate          # Windows
pip install -r requirements.txt
# Créer .env avec les variables listées section 14 ([VALEUR MASQUÉE])
python manage.py migrate
python manage.py runserver
```

Imports données (optionnel) :

```bash
python manage.py import_catalog_v4 --clear
python manage.py import_search_tags_exhaustif --clear
python manage.py import_suppliers --file <chemin.xlsx>
python manage.py import_knowledge_base --file user/tech_attr_paknomik.xlsx
```

### 16.2 Frontend

```bash
cd "paknomik frontend"
npm install
# .env : VITE_DJANGO_URL=http://localhost:8000
npm run dev
```

Application : `http://localhost:5173` (React) + `http://localhost:8000` (Django).

### 16.3 Superutilisateur

```bash
python manage.py createsuperuser
```

---

## 17. Fonctionnalités terminées, incomplètes ou défectueuses

### ✅ Terminées (code présent et branché)

- API catalogue (catégories, sous-catégories, attributs).
- API fournisseurs / produits / recherche.
- Auth JWT + register + OAuth Google (flux API).
- Appels d’offre API + emails fournisseurs.
- RFQ API.
- CRUD produits fournisseur (Django templates).
- Import catalogue v4 et mots-clés search tags.
- Reset password Django (`/auth/`) + templates.
- Recherche produits enrichie (catégorie, sous-catégorie, tags, description).

### ⚠️ Incomplètes

- Pages React fournisseur (produits, commandes, contacts, marché) — placeholders.
- Mot de passe oublié React — **non connecté** au backend.
- Newsletter — API sans UI React.
- Segments commerciaux / matrice catalogue v4 — non importés.
- `SupplierProfileUpdateForm` — défini, non utilisé.
- Templates inquiries/media — orphelins.
- Upload images produit — **à confirmer** dans parcours fournisseur web (admin oui, form utilisateur non évident).
- Double UI Django + React — parcours non unifiés.

### ❌ Défectueuses / dette technique (constat code)

- Tests `user/tests.py` : `Supplier(admin=...)` — champ **`admin` inexistant** sur modèle → tests probablement cassés.
- Conflit URL name `signup` (home vs user).
- `LoginView_email` non routé.
- Dashboard fournisseur API : métriques **calculées / simulées** (pas de tracking réel visites/clics).
- Serializer : notes et avis **hardcodés**.
- Filtre pays API : non-Algeria → `qs.none()` (comportement restrictif).
- `contact_supplier` test attend 200 mais vue redirige (302) vers WhatsApp/mailto.

---

## 18. Améliorations recommandées

1. **Unifier l’UI** : choisir React OU Django templates, ou documenter clairement le périmètre de chaque.
2. **Brancher forgot password React** → `/auth/password_reset/` ou endpoint API dédié.
3. **CRUD produits React** pour fournisseurs + upload images.
4. **Implémenter journalisation** (audit trail RFQ, appels d’offre, login).
5. **Modéliser segments commerciaux** (feuilles Excel v4 non importées).
6. **Corriger tests** et conflit `signup` URL.
7. **Épingler versions** dans `requirements.txt` / `package-lock.json`.
8. **PostgreSQL** en production (settings actuellement SQLite only).
9. **Refresh token JWT** + expiration côté client.
10. **Rate limiting** login / RFQ / newsletter.
11. **Supprimer code mort** : `LoginView_email`, `search_multi.py`, templates orphelins ou les brancher.
12. **Données dashboard réelles** (analytics) vs formules synthétiques actuelles.

---

## 19. Données de démonstration nécessaires

Pour une démo fonctionnelle :

| Donnée | Source / commande |
|--------|-------------------|
| Catégories + sous-catégories + attributs | `import_catalog_v4 --clear` + fichier `user/paknomik_catalog_model_v4.xlsx` |
| Mots-clés recherche | `import_search_tags_exhaustif --clear` + CSV exhaustif |
| Matériaux, industries, lexique | `import_knowledge_base` + Excel |
| Fournisseurs + users | `import_suppliers` ou création manuelle admin |
| Produits avec images | Admin Django ou `/add-product/` + images catégories/sous-catégories |
| Client vérifié | User avec `is_verified_client=True` |
| Appels d’offre test | Via `/appel-offre` (JWT client) |

**Images catégories** : champ `Category.image` — recommandé pour fallback affichage produits sans photo.

---

## 20. Résumé non technique (pour outil de création d’application par IA)

**Paknomik** est une marketplace B2B algérienne (emballages : sacs, bouteilles, cartons, étiquettes, etc.) qui met en relation **acheteurs professionnels** et **fournisseurs d’emballage**.

Les acheteurs parcourent un catalogue structuré par **catégories** et **sous-catégories**, recherchent des produits par nom, description ou mots-clés métier, consultent les fiches fournisseurs et peuvent envoyer une **demande de devis** ou publier un **appel d’offre** décrivant leurs besoins (quantité, spécifications techniques).

Les fournisseurs créent un compte entreprise, renseignent leurs spécialités de catalogue, publient des **produits** (prix indicatif, MOQ, délai) et reçoivent des **notifications par email** quand un appel d’offre correspond à leurs sous-catégories.

L’application existe en **site web React moderne** (recherche, login, dashboard fournisseur) et en **version Django classique** (gestion produits, profils). Tout repose sur un **backend Django** avec API JSON et authentification par **token**.

Couleur de marque : violet **#6c3b9e**. Public cible : industrie, pharma, cosmétique, agroalimentaire, restauration, e-commerce — segments présents dans les mots-clés mais pas tous modélisés en base.

---

# Prompt de reconstruction

Utilise ce prompt pour qu’une autre IA recrée l’application Paknomik de manière fidèle au projet existant.

---

## Contexte

Tu dois reconstruire **Paknomik**, une plateforme web B2B de marketplace d’**emballages** (packaging), orientée marché algérien (wilayas, 🇩🇿). Architecture **Django 5.2 REST API** + **React 18 (Vite, TypeScript, Tailwind, Wouter, React Query)**. Une seconde UI **Django templates** coexiste pour l’administration métier fournisseur (optionnelle à reproduire ou à fusionner dans React).

**Ne pas inventer** : segments commerciaux avancés, paiement en ligne, chat temps réel, analytics réels — ils ne sont pas implémentés.

---

## Objectif produit

1. Référencer fournisseurs d’emballage et leurs produits.
2. Permettre recherche/filtrage par catégorie, sous-catégorie, texte, mots-clés.
3. Permettre aux clients vérifiés de contacter les fournisseurs (RFQ, appels d’offre).
4. Permettre aux fournisseurs de gérer catalogue et voir un dashboard (même partiellement simulé).
5. Maintenir une base de connaissances catalogue importable (Excel/CSV).

---

## Utilisateurs

| Rôle | Identifiants | Droits |
|------|--------------|--------|
| Visiteur | — | Lecture catalogue, recherche |
| Client | `User`, email unique | Inscription, profil, RFQ, appel d’offre si authentifié |
| Client vérifié | `is_verified_client=True` | Contact WhatsApp/email (UI Django) |
| Fournisseur | `User.supplier` → `Supplier` | CRUD produits, dashboard, notifications AO |
| Admin | Django superuser | Tout via admin |

---

## Pages à créer (React — priorité)

1. **Home** `/` — hero, carousel catégories, liste fournisseurs.
2. **Search** `/search` — onglets produits/fournisseurs, filtres query string.
3. **Supplier profile** `/suppliers/:id` — infos, produits, onglet contact/RFQ.
4. **Login** `/login` — login, register, supplier-register ; OAuth Google via redirect backend ; stockage JWT localStorage.
5. **Appel d’offre** `/appel-offre` — formulaire catégorie → sous-catégorie → attributs → POST tender (auth).
6. **Supplier dashboard** `/supplier/dashboard` — KPIs, graphiques (peuvent être dérivés de counts BDD).
7. **Placeholders** fournisseur : products, orders, contacts, market (pages « coming soon » acceptables).

---

## API backend à implémenter

Préfixe `/api/`, JSON camelCase côté serializers.

```
GET  /api/categories/
GET  /api/subcategories/?parent={id|slug}
GET  /api/category-attributes/?category=&subcategory=
GET  /api/suppliers/?q=&category=&country=&verified=&featured=
GET  /api/suppliers/{id}/
GET  /api/products/?q=&category=&supplier_id=&country=
POST /api/auth/login/     { username|email|login, password } → { token, user }
POST /api/auth/register/  { email, password, name, company?, isSupplier? }
GET  /api/auth/google/?redirect=
GET  /api/auth/google/callback/
POST /api/rfq/            { supplierId, name, company, email, message }
POST /api/tender-calls/     JWT — { categoryId, subcategoryId, quantity, description, tenderType, technicalAttributes[] }
POST /api/newsletter/     { email }
GET  /api/supplier/dashboard/?year=&month=  JWT supplier
GET  /api/stats
```

Auth : JWT Bearer, 7 jours. CORS vers frontend. Pas de session API.

---

## Modèles de données essentiels

Implémenter au minimum :

- **User** (AbstractUser + profil client + FK supplier nullable)
- **Supplier** (entreprise, wilaya, type, logo, brand_color, M2M catégories intérêt)
- **Category** (tree level 1/2, slug, name_en/fr, image, external_id v4-*)
- **TechnicalAttribute**, **CategoryAttribute**, **AttributeOption**
- **Material**, **Unit**, **Industry**
- **SearchTag** (tag, tag_fr, tag_ar, weight) M2M **Product**
- **Product** (supplier, category, subcategory, material, prix, MOQ, lead_time, flags)
- **ProductImage**, **ProductAttributeValue**
- **RFQRequest**, **NewsletterSubscriber**
- **TenderCall**, **TenderCallAttributeValue**, **TenderCallNotification**

Règle image produit : si pas d’image uploadée → utiliser `subcategory.image` puis `category.image`.

Règle recherche produit : matcher name, description, category/subcategory names+slugs, search_tags (en/fr/ar).

---

## Règles métier

1. Un **appel d’offre** notifie les fournisseurs ayant la sous-catégorie en `interested_subcategories`, sinon ceux ayant des produits actifs dans cette sous-catégorie.
2. **Tender type detailed** : valider attributs techniques contre `CategoryAttribute` de la catégorie/sous-catégorie.
3. **Client vérifié** requis pour `contact_supplier` (redirect WhatsApp/mailto) en UI Django.
4. **Fournisseur** requis pour dashboard API (`user.supplier_id`).
5. Catalogue import : commandes management idempotentes (`update_or_create`, external_id `v4-`, `v4-sub-`, `v4-pharma-`).
6. API liste pays : filtre `country` non Algeria/DZ/all retourne vide (comportement actuel).

---

## Auth & sécurité

- django-allauth pour `/accounts/` (login web, social).
- `/auth/` pour reset password Django (templates registration/).
- Variables sensibles via `.env` — **[VALEUR MASQUÉE]** dans la doc.
- CSRF trusted origins = frontend URL.
- Ne jamais exposer mots de passe en clair dans logs ou docs.

---

## Imports / seed

Commandes :

- `import_catalog_v4` ← `paknomik_catalog_model_v4.xlsx`
- `import_search_tags_exhaustif` ← CSV mots-clés
- `import_suppliers` ← Excel
- `import_knowledge_base` ← `tech_attr_paknomik.xlsx`

---

## Design

- Couleur principale : **#6c3b9e**
- Fonts : Inter, Quicksand, Poppins
- UI : cartes arrondies, champs `auth-input`, boutons `auth-button`
- Font Awesome 6 icônes

---

## Ce qu’il ne faut PAS reconstruire sans spec additionnelle

- Paiement, commandes e-commerce réelles, logistique.
- Segments commerciaux / matrice cat-segment (non en BDD).
- Analytics visites/clics réels (dashboard utilise formules).
- Journalisation utilisateur (absente).
- Workers Celery (absents).
- Modèle Service (non utilisé).

---

## Critères d’acceptation minimaux

- [ ] Un visiteur voit catégories et fournisseurs sur Home.
- [ ] Recherche produits fonctionne avec mots-clés SearchTag.
- [ ] Login/register JWT + Google OAuth redirect.
- [ ] Fournisseur voit dashboard API authentifié.
- [ ] Client authentifié peut créer un appel d’offre et déclencher email fournisseur (console en dev).
- [ ] Admin Django gère catalogue et produits.
- [ ] Import v4 peuple catégories et attributs.

---

*Fin du document.*
