# Déployer Paknomik sur Railway

Deux dépôts GitHub, trois services Railway. Le développement local reste sur SQLite et `npm run dev`. Aucun secret ne doit être commité.

| Service Railway | Dépôt | Rôle |
|---|---|---|
| PostgreSQL | plugin Railway | Base de production |
| Backend | `paknomik backend` | Django, Gunicorn, module WSGI `paknomik.wsgi:application` |
| Frontend | `paknomik frontend` | Vite + React, routage client Wouter, servi par `serve -s` |

## 1. PostgreSQL

1. Dans le projet Railway, ajoutez le plugin **PostgreSQL**.
2. Railway crée `DATABASE_URL`. Ne la copiez pas dans Git.
3. Reliez cette variable au service backend (Reference Variable), pas au frontend.

Sans `DATABASE_URL`, Django continue d'utiliser `db.sqlite3` en local seulement. Sur Railway, l'absence de `DATABASE_URL` arrête le démarrage : le fichier SQLite local ne doit jamais servir en production.

## 2. Backend Django

Racine du service : le dossier qui contient `manage.py`.

Railway lit `railway.toml` :

- avant le démarrage : `migrate --noinput` puis `collectstatic --noinput`
- démarrage : `gunicorn paknomik.wsgi:application` sur `$PORT`

Python visé : 3.12 (`runtime.txt`). Les versions épinglées sont celles qui tournent en local, dont Django 4.2.30.

### Variables du service backend

| Variable | Valeur |
|---|---|
| `DJANGO_SECRET_KEY` | clé longue aléatoire. Obligatoire si `DJANGO_DEBUG=false` |
| `DJANGO_DEBUG` | `false` |
| `DJANGO_ALLOWED_HOSTS` | domaine public du backend, sans schéma. Railway ajoute aussi `RAILWAY_PUBLIC_DOMAIN` et `healthcheck.railway.app` |
| `DATABASE_URL` | référence vers Postgres |
| `BACKEND_URL` | `https://` + domaine public du backend |
| `FRONTEND_URL` | `https://` + domaine public du frontend |
| `APP_BASE_URL` | même URL que `FRONTEND_URL` |
| `CORS_ALLOWED_ORIGINS` | URL publique du frontend, sans slash final |
| `CSRF_TRUSTED_ORIGINS` | URL publique du frontend et URL publique du backend |
| `DJANGO_SECURE_COOKIES` | `true` |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | `true` (activé aussi quand `RAILWAY_ENVIRONMENT` est présent) |
| `VISITOR_COOKIE_SAMESITE` | `None` tant que le frontend et l'API sont sur deux domaines |
| `TOKEN_ENCRYPTION_KEY` | clé Fernet. Les jetons déjà chiffrés deviennent illisibles si elle change |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | mêmes valeurs que l'app Google OAuth |
| `GOOGLE_REDIRECT_URI` | `https://<backend>/api/integrations/oauth/google/callback/` |
| `META_*`, `INSTAGRAM_*` | uniquement si la présence digitale est branchée |
| `MEDIA_ROOT` | chemin d'un volume persistant, par exemple `/data/media` |
| `DJANGO_SERVE_MEDIA` | `true` seulement après avoir monté ce volume |
| `DJANGO_LOG_LEVEL` | `INFO` |

Générez la clé Django avec :

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Médias

Le disque Railway est éphémère. Les photos, logos et PDF déposés dans `media/` disparaissent à chaque redéploiement si aucun volume n'est monté.

1. Ajoutez un volume au service backend, monté sur `/data`.
2. `MEDIA_ROOT=/data/media`
3. `DJANGO_SERVE_MEDIA=true`

WhiteNoise sert les fichiers statiques collectés. Il ne sert pas les médias. Sans volume, laissez `DJANGO_SERVE_MEDIA=false` : les uploads ne doivent pas être considérés comme durables.

Un stockage objet (S3 ou équivalent) n'est pas branché. Le modèle de fichiers Django reste le stockage local.

### Données

L'hébergement démarre sur une base PostgreSQL vide, puis charge les fournisseurs et les produits. Le `preDeployCommand` applique les migrations, puis `import_hosting_catalog`.

Ce catalogue (`hosting_catalog/`) contient les fiches fournisseurs, leurs produits, les logos et les photos produit. Il n'exporte ni n'importe les comptes utilisateurs ou leurs données d'authentification. Il ne contient pas les commandes, messages, avis, campagnes, ni le journal Pragmadinar. Chaque fournisseur importé reçoit une seule fois le crédit de bienvenue de 200. Si des fournisseurs existent déjà, l'import ne réécrit pas les fiches.

Régénérer le catalogue depuis la base locale, avant l'envoi :

```bash
python manage.py export_hosting_catalog
```

Ne pas envoyer :

- `db.sqlite3` et ses sauvegardes (historique de travail)
- le dossier `media/` entier (commandes, messages, devis)
- `.env`
- un export `dumpdata`, `.sql` ou `.dump`

`.gitignore` exclut `hosting_catalog/` du dépôt Git pour éviter d'y stocker les données fournisseurs. Transférez ce catalogue vers l'environnement d'hébergement par un canal privé avec accès limité. `.railwayignore` exclut les bases locales, les exports SQL et le dossier `media/`. Un second déploiement ne recharge pas les fiches déjà présentes. Les photos du catalogue sont recopiées.

### Après le premier déploiement

1. `python manage.py createsuperuser` dans le shell Railway.
2. Renseignez les URL autorisées Google : origine JavaScript du frontend, redirection `https://<backend>/accounts/google/login/callback/`.
3. Les migrations existantes sont appliquées par `preDeployCommand`. N'exécutez pas `flush`, `squash` ni une migration vers une base de production depuis la machine locale.

## 3. Frontend React

Le frontend n'est pas encore un dépôt Git. Créez-le à la racine de `paknomik frontend`, qui contient `package.json` et `vite.config.ts`.

Outil de build : Vite 5. Routage : Wouter, entièrement dans le navigateur. `railway.toml` lance `npm run build` (`tsc -b` puis `vite build`) puis `serve dist -s`, qui renvoie `index.html` pour les routes `/supplier/...`. Une erreur TypeScript bloque donc le déploiement.

Les composants shadcn non utilisés dont les paquets ne sont pas installés (`calendar`, `chart`, `carousel`, etc.) sont exclus du contrôle de types dans `tsconfig.json`. Pour en utiliser un, installez son paquet et retirez-le de la liste `exclude`.

Les URL de médias et de fiches techniques sont préfixées par `VITE_API_BASE_URL`. Les images sont donc chargées depuis le domaine du backend, qui doit les servir (voir « Médias »).

### Variables du service frontend

Ces variables sont intégrées au bundle au moment du build. Ne mettez aucun secret.

| Variable | Local | Production |
|---|---|---|
| `VITE_API_BASE_URL` | vide, le proxy Vite relaie `/api` | `https://<backend>` sans slash final |
| `VITE_DJANGO_URL` | `http://localhost:8000` | même URL publique que l'API |
| `VITE_GOOGLE_CLIENT_ID` | identifiant public OAuth, si le popup GIS est utilisé | le même identifiant |
| `VITE_GOOGLE_USE_GIS` | `false` | `false` sauf si le popup Google est voulu |

Railway doit relancer un build après chaque changement de variable `VITE_*`.

Le fallback `http://localhost:8000` de `VITE_DJANGO_URL` reste en place pour le développement. En production les deux variables doivent être définies avant le build, sinon le navigateur appellera la machine locale.

## 4. Ordre de mise en ligne

1. Plugin PostgreSQL.
2. Service backend, dépôt GitHub du backend, variables ci-dessus, volume médias si les fichiers doivent survivre.
3. Vérifiez `https://<backend>/admin/login/`.
4. Service frontend, variables `VITE_*`, puis le domaine public.
5. Recollez `FRONTEND_URL`, `APP_BASE_URL`, `CORS_ALLOWED_ORIGINS` et `CSRF_TRUSTED_ORIGINS` avec ce domaine, puis redéployez le backend.

Derrière le proxy Railway, `USE_X_FORWARDED_HOST` est activé en même temps que `SECURE_PROXY_SSL_HEADER`. Les URL absolues (médias, redirections OAuth) utilisent alors le domaine public en HTTPS. En local, sans `RAILWAY_ENVIRONMENT`, ce comportement ne change pas.

Le build frontend échoue si Railway est détecté et que `VITE_API_BASE_URL` est vide. Sans cette variable, le navigateur enverrait `/api` au service statique.

## 5. Checklist avant déploiement

Vérifiée le 9 octobre 2026.

| Point | État | Vérification |
|---|---|---|
| Les paramètres Django fonctionnent en local | OK | `python manage.py check` : aucun problème signalé |
| SQLite reste utilisable en développement | OK | sans `DATABASE_URL`, le moteur est `sqlite3` sur `db.sqlite3` |
| PostgreSQL utilise `DATABASE_URL` | OK | URL de test décodée : utilisateur, mot de passe encodé, port et `?sslmode=require` passent dans `DATABASES`. `psycopg` 3.2.9 est épinglé |
| Les secrets ne sont pas dans Git | OK | `.env`, `*.sqlite3`, les sauvegardes `*.sqlite3.bak*`, `media/` et `*.mmdb` sont ignorés. Aucune clé en dur dans le code |
| Le build React de production réussit | OK | `npm run build` passe. Un build avec une URL d'API de production l'intègre au bundle, et `localhost:8000` n'y apparaît plus |
| Le frontend utilise une URL d'API configurable | OK | `VITE_API_BASE_URL` pour l'API, les médias et les fiches techniques. Build Railway refusé si elle est vide |
| Les migrations existantes sont préservées | OK | aucune migration supprimée, `makemigrations --check` ne détecte rien, toutes sont appliquées en local jusqu'à `0112` |
| Ce fichier explique les étapes restantes | OK | section 6 |

`check --deploy` avec `DJANGO_DEBUG=false` ne signale que `SECURE_HSTS_SECONDS` et `SECURE_SSL_REDIRECT`. Railway force déjà HTTPS. Activez HSTS seulement une fois le domaine définitif en place.

## 6. Étapes restantes

1. Créer les deux dépôts GitHub. Avant le premier commit du backend, vérifiez avec `git status` qu'aucun `.env`, `db.sqlite3` ni `db.sqlite3.bak-*` n'apparaît.
2. Initialiser Git dans `paknomik frontend` (il n'en a pas encore).
3. Créer le projet Railway et suivre l'ordre de la section 4.
4. Générer `DJANGO_SECRET_KEY` et `TOKEN_ENCRYPTION_KEY` directement dans Railway. Ne réutilisez pas les valeurs du `.env` local.
5. Monter un volume pour `MEDIA_ROOT` avant d'accepter des uploads réels.
6. Transférer `hosting_catalog/` par un canal privé vers l'environnement d'hébergement. Il contient uniquement les fiches et produits sélectionnés, sans comptes utilisateurs. Ne pas copier `db.sqlite3` ni `media/`. Pas de `dumpdata` / `loaddata`.
7. Mettre à jour les URL de redirection Google, Meta et Instagram avec le domaine du backend.
8. Après le premier déploiement : `createsuperuser`, puis vérifier `/admin/login/`, une connexion, une recherche, une image produit et une fiche technique PDF.

Points connus, sans blocage :

- Aucun cache partagé n'est configuré. Chaque worker Gunicorn garde son propre cache mémoire, vide au démarrage.
- Le bundle JavaScript principal pèse environ 1,2 Mo (330 ko gzip). Vite le signale, sans erreur.
