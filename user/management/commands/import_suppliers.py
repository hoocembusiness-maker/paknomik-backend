"""
Import suppliers from user/paknomik data base.xlsx

For each row:
  1. Create or update admin User (username + password from Excel)
  2. Create or update Supplier and link via user.supplier

Usage:
  python manage.py import_suppliers --dry-run
  python manage.py import_suppliers
  python manage.py import_suppliers --update
  python manage.py import_suppliers --file "user/paknomik data base.xlsx"
"""

from pathlib import Path
import re
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user.models import User, Supplier

try:
    import openpyxl
except ImportError as exc:  # pragma: no cover
    raise CommandError(
        'openpyxl is required. Install it with: pip install openpyxl'
    ) from exc


WILAYA_MAP = {
    'adrar': 'adrar',
    'alger': 'algiers',
    'algiers': 'algiers',
    'annaba': 'annaba',
    'constantine': 'constantine',
    'oran': 'oran',
    'setif': 'setif',
    'sétif': 'setif',
    'tizi ouzou': 'tizi_ouzou',
    'tizi_ouzou': 'tizi_ouzou',
    'blida': 'blida',
    'bejaia': 'bejaia',
    'béjaia': 'bejaia',
    'bajaia': 'bejaia',
    'batna': 'batna',
    'biskra': 'biskra',
    'other': 'other',
}

PLACEHOLDER_EMAIL_DOMAIN = 'imported.paknomik.local'


def _normalize_text(value):
    if value is None:
        return ''
    text = str(value).strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return text


def normalize_wilaya(raw):
    if raw is None or str(raw).strip() == '':
        return None
    key = _normalize_text(raw).lower()
    key = re.sub(r'\s+', ' ', key)
    mapped = WILAYA_MAP.get(key)
    if mapped:
        return mapped
    slug = key.replace(' ', '_').replace('-', '_')
    valid = {choice[0] for choice in Supplier.WILAYA_CHOICES}
    if slug in valid:
        return slug
    return 'other'


EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')


def normalize_website(raw):
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered in {'no website', 'none', 'n/a', 'na', '-', '/', 'no page'}:
        return None
    if not re.match(r'^https?://', value, re.IGNORECASE):
        value = f'https://{value}'
    return value


def placeholder_email(username):
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', str(username).strip().lower())
    return f'{safe}@{PLACEHOLDER_EMAIL_DOMAIN}'


def normalize_email(raw, username):
    if raw is None:
        return placeholder_email(username)
    value = str(raw).strip()
    if not value:
        return placeholder_email(username)
    value = re.split(r'[\n;,]', value)[0].strip()
    if EMAIL_RE.match(value):
        return value
    return placeholder_email(username)


class Command(BaseCommand):
    help = 'Import suppliers and linked users from the Paknomik Excel file'

    def add_arguments(self, parser):
        default_file = (
            Path(__file__).resolve().parents[2] / 'paknomik data base.xlsx'
        )
        parser.add_argument(
            '--file',
            type=str,
            default=str(default_file),
            help='Path to the Excel file',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Validate and print actions without writing to the database',
        )
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update existing users/suppliers when username or email exists',
        )

    def handle(self, *args, **options):
        file_path = Path(options['file'])
        dry_run = options['dry_run']
        update = options['update']

        if not file_path.exists():
            raise CommandError(f'Excel file not found: {file_path}')

        workbook = openpyxl.load_workbook(file_path, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise CommandError('Excel file is empty')

        header = [(_normalize_text(h).lower() if h is not None else '') for h in rows[0]]

        created_users = 0
        created_suppliers = 0
        updated = 0
        skipped = 0
        errors = 0

        for index, row in enumerate(rows[1:], start=2):
            if not row or not row[0]:
                continue

            company_name = str(row[0]).strip()
            email_raw = row[1] if len(row) > 1 else None
            website = normalize_website(row[2] if len(row) > 2 else None)
            wilaya_raw = row[3] if len(row) > 3 else None
            username = str(row[5]).strip() if len(row) > 5 and row[5] else ''
            password = str(row[6]).strip() if len(row) > 6 and row[6] else ''

            if not username or not password:
                self.stdout.write(
                    self.style.ERROR(
                        f'Row {index}: missing username/password for {company_name!r}'
                    )
                )
                errors += 1
                continue

            email = normalize_email(email_raw, username)
            wilaya = normalize_wilaya(wilaya_raw)
            if wilaya_raw and wilaya == 'other' and _normalize_text(wilaya_raw):
                self.stdout.write(
                    self.style.WARNING(
                        f'Row {index}: wilaya {wilaya_raw!r} mapped to "other" '
                        f'for {company_name!r}'
                    )
                )

            action = 'create'
            try:
                with transaction.atomic():
                    user = User.objects.filter(username=username).first()
                    if user is None:
                        user = User.objects.filter(email__iexact=email).first()

                    if user is None:
                        if dry_run:
                            self.stdout.write(
                                f'[dry-run] CREATE user {username!r} / {email!r}'
                            )
                        else:
                            user = User.objects.create_user(
                                username=username,
                                email=email,
                                password=password,
                            )
                        created_users += 1
                    else:
                        if not update:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'Row {index}: user already exists '
                                    f'({user.username} / {user.email}) — skip '
                                    f'(use --update to refresh)'
                                )
                            )
                            skipped += 1
                            continue
                        action = 'update'
                        if dry_run:
                            self.stdout.write(
                                f'[dry-run] UPDATE user {user.username!r} / {user.email!r}'
                            )
                        else:
                            user.username = username
                            user.email = email
                            user.set_password(password)
                            user.save()
                        updated += 1

                    supplier = None if dry_run else user.supplier
                    if supplier is None and not dry_run:
                        supplier = Supplier.objects.filter(email__iexact=email).first()
                    if supplier is None and not dry_run:
                        supplier = Supplier.objects.filter(
                            company_name__iexact=company_name
                        ).first()

                    if dry_run:
                        self.stdout.write(
                            f'[dry-run] {action.upper()} supplier {company_name!r} '
                            f'(wilaya={wilaya}, website={website})'
                        )
                        if action == 'create':
                            created_suppliers += 1
                        continue

                    if supplier is None:
                        supplier = Supplier.objects.create(
                            company_name=company_name,
                            email=email,
                            website=website,
                            wilaya=wilaya,
                            is_active=True,
                        )
                        created_suppliers += 1
                    else:
                        supplier.company_name = company_name
                        supplier.email = email
                        supplier.website = website
                        supplier.wilaya = wilaya
                        supplier.is_active = True
                        supplier.save()
                        if action != 'update':
                            updated += 1

                    if user.supplier_id != supplier.id:
                        user.supplier = supplier
                        user.save(update_fields=['supplier'])

                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Row {index}: {action}d {company_name!r} '
                            f'(user={username}, email={email})'
                        )
                    )
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f'Row {index}: {company_name!r} failed — {exc}')
                )
                errors += 1

        self.stdout.write('')
        self.stdout.write(
            self.style.NOTICE(
                f'Done. users_created={created_users}, '
                f'suppliers_created={created_suppliers}, '
                f'updated={updated}, skipped={skipped}, errors={errors}'
                + (' [DRY RUN]' if dry_run else '')
            )
        )
