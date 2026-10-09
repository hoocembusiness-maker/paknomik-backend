# Paknomik - Codex AI Rules

project:
  name: Paknomik
  framework: Django 5.2
  database: SQLite (development), PostgreSQL (production)
  auth: django-allauth (email + Google + LinkedIn)
  frontend: Django templates + custom CSS (no Tailwind)
  styling: style_exported.css, style_login.css
  user_model: user.User (custom)
  apps:
    - user (authentication, profiles, suppliers, products)
    - home (landing, static pages)
    - accounts (legacy? keep only if needed)

general:
  language: English (code, comments) / Arabic possible in user-facing strings
  line_length: 100
  indent: 2 spaces (HTML) / 4 spaces (Python)
  quotes: single quotes for Python, double for HTML attributes

python:
  max_line_length: 100
  imports: group (stdlib, third-party, local)
  use_fstrings: true
  type_hints: optional but encouraged
  avoid:
    - wildcard imports
    - bare except:
    - modifying settings.py without asking

django:
  models:
    - always define __str__
    - use related_name explicitly
    - set on_delete=models.CASCADE or PROTECT
  views:
    - use @login_required where needed
    - use @verified_client_required for client-only views
    - use @supplier_required for supplier-only views
  forms:
    - use ModelForm when possible
    - add widgets and labels for branding
  templates:
    - extend base.html or user/base_auth.html
    - use static tag for assets
    - use url tag with namespaces e.g. 'user:complete_profile'
  migrations:
    - never delete migration files manually
    - run makemigrations before asking to migrate

frontend:
  css_classes: existing (auth-input, auth-button, dashboard-card, etc.)
  responsive: use media queries (max-width: 768px)
  icons: Font Awesome 6 (free)
  fonts: Inter, Quicksand, Poppins

user_roles:
  - Simple Client (is_verified_client=False, default)
  - Verified Client (is_verified_client=True, after profile)
  - Supplier (has admin_supplier relation)

file_restrictions:
  never_delete:
    - settings.py
    - urls.py (main)
    - models.py (any existing field)
    - any migration file
  ask_before_overwrite:
    - views.py
    - forms.py
    - templates/**/*.html
    - static/css/*.css

commands:
  - use 'python manage.py runserver' to start
  - use 'python manage.py makemigrations' then 'migrate' for DB changes
  - do not run collectstatic unless asked

behavior:
  - when asked to add a feature, first check if similar code exists
  - propose code changes before applying (diff)
  - explain changes briefly
  - keep existing branding (purple #6c3b9e, rounded cards)
  - for new features, place models in user/models.py, views in user/views.py
  - always include proper error handling and user messages

templates:
  base: templates/base.html
  auth_pages: templates/user/base_auth.html (extends base.html)
  prefer using existing CSS classes over inline styles when possible

api:
  - internal API endpoints prefixed with /api/
  - return JSON responses with JsonResponse

testing:
  - write minimal tests for critical paths (login, registration, product add)
  - use django.test.TestCase

git:
  - commit messages: present tense, short (<50 chars)
  - never commit .env, db.sqlite3, __pycache__, media/ uploads

always_include:
  - import statements
  - CSRF token in POST forms
  - enctype="multipart/form-data" for image uploads

avoid:
  - using raw SQL without reason
  - hardcoding URLs (use reverse or url tag)
  - creating new apps without approval
  - changing AUTH_USER_MODEL after migrations are applied