# Paknomik Backend

Backend web de Paknomik, construit avec Django. Le projet fournit les pages du site,
l'authentification, les espaces clients et fournisseurs, ainsi que l'API consommée par le
frontend React.

## Stack

- Python 3.12
- Django (version épinglée dans `requirements.txt`)
- SQLite en développement ; PostgreSQL via `DATABASE_URL` en production
- Django REST Framework, django-allauth, Gunicorn et WhiteNoise

## Prérequis

- Python 3.12
- Git

## Installation locale

Depuis le dossier qui contient `manage.py` :

```bash
python -m venv .venv
```

Activez l'environnement virtuel, puis installez les dépendances :

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

En local, Django utilise SQLite et les paramètres de développement par défaut. Le fichier
`.env.example` répertorie les variables disponibles, mais Django ne charge pas ce fichier
automatiquement : définissez les variables nécessaires dans votre environnement avant de
lancer les commandes.

Appliquez les migrations et démarrez le serveur :

```bash
python manage.py migrate
python manage.py runserver
```

Le serveur local est accessible à <http://127.0.0.1:8000/>. Pour créer un compte
d'administration :

```bash
python manage.py createsuperuser
```

## Structure

- `paknomik/` : configuration Django et points d'entrée ASGI/WSGI
- `user/` : comptes, profils, fournisseurs, produits et API
- `home/` : pages d'accueil et pages publiques
- `accounts/` : application historique encore présente dans le projet
- `templates/` : templates Django
- `docs/` : documentation fonctionnelle et ressources du projet

## Configuration

Les principaux paramètres sont fournis par variables d'environnement :

| Variable | Utilisation |
| --- | --- |
| `DJANGO_SECRET_KEY` | Clé secrète Django ; requise lorsque `DJANGO_DEBUG=false` |
| `DJANGO_DEBUG` | Active ou désactive le mode debug |
| `DJANGO_ALLOWED_HOSTS` | Hôtes autorisés, séparés par des virgules |
| `DATABASE_URL` | Connexion PostgreSQL ; sans cette variable, SQLite est utilisé en local |
| `CORS_ALLOWED_ORIGINS` | Origines frontend autorisées par CORS |
| `CSRF_TRUSTED_ORIGINS` | Origines de confiance pour la protection CSRF |
| `FRONTEND_URL` / `BACKEND_URL` | Adresses publiques du frontend et du backend |
| `TOKEN_ENCRYPTION_KEY` | Chiffrement des jetons d'intégration |

Les intégrations OAuth et les options de stockage média ont aussi leurs variables dédiées dans
`.env.example`. Ne commitez jamais `.env`, les secrets, les bases SQLite ou les fichiers
utilisateur du dossier `media/`.

## Déploiement

Le backend est configuré pour Railway avec `railway.toml`. Le déploiement applique les
migrations, importe le catalogue d'hébergement, collecte les fichiers statiques puis démarre
Gunicorn. Il faut configurer les secrets et `DATABASE_URL` dans Railway. Les médias nécessitent
un volume persistant pour survivre aux redéploiements.

Consultez [DEPLOY_RAILWAY.md](DEPLOY_RAILWAY.md) pour les variables, le catalogue, les médias et
l'ordre complet de mise en ligne.
