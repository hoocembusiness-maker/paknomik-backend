"""
Import v2 catalog categories and technical attributes from paknomik_catalog.xlsx
(English) and paknomik_catalog_fr.xlsx (French translations).

Sheets used:
  - 1_Technical_Categories / 1_Catégories_techniques  -> Category
  - 4_Level1_Search_Filters / 4_Filtres_recherche_N1   -> TechnicalAttribute L1
  - 5_Level2_Product_Specs / 5_Fiches_produit_N2      -> TechnicalAttribute L2

Also creates CategoryAttribute links and AttributeOption rows for Level 1 dropdowns.

Usage:
  python manage.py import_catalog_v2 --dry-run
  python manage.py import_catalog_v2
  python manage.py import_catalog_v2 --clear
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    TechnicalAttribute,
    Unit,
)

try:
    import openpyxl
except ImportError as exc:  # pragma: no cover
    raise CommandError(
        'openpyxl is required. Install it with: pip install openpyxl'
    ) from exc


NEW_IN_V2_CATEGORY_IDS = {4, 5, 6, 10}


def cell_str(value):
    if value is None:
        return ''
    return str(value).strip()


def sheet_rows(workbook, index):
    sheet = workbook.worksheets[index]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def parse_options(raw):
    text = cell_str(raw)
    if not text:
        return []
    return [part.strip() for part in text.split('|') if part.strip()]


def map_input_type(raw, level):
    text = cell_str(raw).lower()
    if level == 1:
        if text == 'dropdown':
            return 'choice'
        return 'text'
    if text == 'number':
        return 'number'
    if text == 'boolean':
        return 'boolean'
    if text in {'choice', 'dropdown', 'liste'}:
        return 'choice'
    return 'text'


def category_external_id(category_id):
    return f'v2-{int(category_id):02d}'


def attribute_external_id(category_id, level, sort_order):
    return f'v2-{int(category_id):02d}-L{level}-{sort_order:02d}'


class Command(BaseCommand):
    help = (
        'Import Category and TechnicalAttribute data from '
        'paknomik_catalog.xlsx and paknomik_catalog_fr.xlsx'
    )

    def add_arguments(self, parser):
        base_dir = Path(__file__).resolve().parents[3]
        parser.add_argument(
            '--file-en',
            type=str,
            default=str(base_dir / 'paknomik_catalog.xlsx'),
            help='Path to the English catalog workbook',
        )
        parser.add_argument(
            '--file-fr',
            type=str,
            default=str(base_dir / 'paknomik_catalog_fr.xlsx'),
            help='Path to the French catalog workbook',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to the database',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing v2 catalog rows before import',
        )

    def handle(self, *args, **options):
        file_en = Path(options['file_en'])
        file_fr = Path(options['file_fr'])
        dry_run = options['dry_run']
        clear = options['clear']

        for path in (file_en, file_fr):
            if not path.exists():
                raise CommandError(f'Excel file not found: {path}')

        workbook_en = openpyxl.load_workbook(file_en, data_only=True)
        workbook_fr = openpyxl.load_workbook(file_fr, data_only=True)

        stats = {}

        if clear and not dry_run:
            self._clear_v2_catalog()

        with transaction.atomic():
            category_map = self._import_categories(
                workbook_en, workbook_fr, dry_run
            )
            stats['categories'] = len(category_map)
            stats['level_1'] = self._import_level_1_attributes(
                workbook_en, workbook_fr, category_map, dry_run
            )
            stats['level_2'] = self._import_level_2_attributes(
                workbook_en, workbook_fr, category_map, dry_run
            )

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS('Import summary:'))
        for key, value in stats.items():
            self.stdout.write(f'  {key}: {value}')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes saved'))

    def _clear_v2_catalog(self):
        AttributeOption.objects.all().delete()
        CategoryAttribute.objects.all().delete()
        TechnicalAttribute.objects.filter(external_id__startswith='v2-').delete()
        Category.objects.filter(external_id__startswith='v2-').delete()
        self.stdout.write(self.style.WARNING('Cleared v2 catalog data'))

    def _import_categories(self, workbook_en, workbook_fr, dry_run):
        header_en, rows_en = sheet_rows(workbook_en, 0)
        header_fr, rows_fr = sheet_rows(workbook_fr, 0)

        if len(rows_en) != len(rows_fr):
            raise CommandError('Category row count mismatch between EN and FR files')

        category_map = {}
        for row_en, row_fr in zip(rows_en, rows_fr):
            category_id = row_en[0]
            if category_id is None:
                continue

            external_id = category_external_id(category_id)
            defaults = {
                'slug': cell_str(row_en[1]),
                'sort_order': int(category_id),
                'name_en': cell_str(row_en[2]),
                'name_fr': cell_str(row_fr[2]),
                'plain_description': cell_str(row_en[3]),
                'is_new_in_v2': int(category_id) in NEW_IN_V2_CATEGORY_IDS,
                'level': 1,
                'parent': None,
            }

            if dry_run:
                category_map[int(category_id)] = external_id
                continue

            category, _ = Category.objects.update_or_create(
                external_id=external_id,
                defaults=defaults,
            )
            category_map[int(category_id)] = category

        return category_map

    def _group_rows_by_category(self, rows):
        grouped = {}
        for row in rows:
            category_id = row[0]
            if category_id is None:
                continue
            grouped.setdefault(int(category_id), []).append(row)
        return grouped

    def _resolve_unit(self, raw):
        symbol = cell_str(raw)
        if not symbol:
            return None
        unit = Unit.objects.filter(symbol__iexact=symbol).first()
        if unit:
            return unit
        unit = Unit.objects.filter(name__iexact=symbol).first()
        if unit:
            return unit
        unit, _ = Unit.objects.get_or_create(
            name=symbol,
            defaults={'symbol': symbol},
        )
        return unit

    def _import_level_1_attributes(
        self, workbook_en, workbook_fr, category_map, dry_run
    ):
        _, rows_en = sheet_rows(workbook_en, 3)
        _, rows_fr = sheet_rows(workbook_fr, 3)

        if len(rows_en) != len(rows_fr):
            raise CommandError('Level 1 row count mismatch between EN and FR files')

        grouped_en = self._group_rows_by_category(rows_en)
        grouped_fr = self._group_rows_by_category(rows_fr)
        stats = {'attributes': 0, 'links': 0, 'options': 0}

        for category_id, category_rows_en in grouped_en.items():
            category_rows_fr = grouped_fr.get(category_id, [])
            if len(category_rows_en) != len(category_rows_fr):
                raise CommandError(
                    f'Level 1 row count mismatch for category {category_id}'
                )

            for sort_order, (row_en, row_fr) in enumerate(
                zip(category_rows_en, category_rows_fr), start=1
            ):
                stats['attributes'] += 1
                stats['links'] += 1
                options_en = parse_options(row_en[5])
                options_fr = parse_options(row_fr[5])
                stats['options'] += len(options_en)

                if dry_run:
                    continue

                category = category_map[category_id]
                external_id = attribute_external_id(category_id, 1, sort_order)
                attribute_type = map_input_type(row_en[4], level=1)

                attribute, _ = TechnicalAttribute.objects.update_or_create(
                    external_id=external_id,
                    defaults={
                        'name_en': cell_str(row_en[3]),
                        'name_fr': cell_str(row_fr[3]),
                        'attribute_level': 1,
                        'attribute_type': attribute_type,
                        'is_required': True,
                        'is_searchable': attribute_type == 'choice',
                        'is_comparable': False,
                    },
                )

                link, _ = CategoryAttribute.objects.update_or_create(
                    category=category,
                    attribute=attribute,
                    defaults={
                        'sort_order': sort_order,
                        'is_required_for_publish': True,
                    },
                )

                if attribute_type != 'choice':
                    continue

                for opt_idx, option_en in enumerate(options_en, start=1):
                    option_fr = ''
                    if opt_idx - 1 < len(options_fr):
                        option_fr = options_fr[opt_idx - 1]
                    AttributeOption.objects.update_or_create(
                        category_attribute=link,
                        value_en=option_en,
                        defaults={
                            'value_fr': option_fr,
                            'sort_order': opt_idx,
                        },
                    )

        return stats

    def _import_level_2_attributes(
        self, workbook_en, workbook_fr, category_map, dry_run
    ):
        _, rows_en = sheet_rows(workbook_en, 4)
        _, rows_fr = sheet_rows(workbook_fr, 4)

        if len(rows_en) != len(rows_fr):
            raise CommandError('Level 2 row count mismatch between EN and FR files')

        grouped_en = self._group_rows_by_category(rows_en)
        grouped_fr = self._group_rows_by_category(rows_fr)
        level_1_counts = {
            category_id: len(rows)
            for category_id, rows in grouped_en.items()
        }
        stats = {'attributes': 0, 'links': 0}

        for category_id, category_rows_en in grouped_en.items():
            category_rows_fr = grouped_fr.get(category_id, [])
            if len(category_rows_en) != len(category_rows_fr):
                raise CommandError(
                    f'Level 2 row count mismatch for category {category_id}'
                )

            level_1_count = level_1_counts.get(category_id, 0)

            for sort_order, (row_en, row_fr) in enumerate(
                zip(category_rows_en, category_rows_fr), start=1
            ):
                stats['attributes'] += 1
                stats['links'] += 1

                if dry_run:
                    continue

                category = category_map[category_id]
                external_id = attribute_external_id(category_id, 2, sort_order)
                attribute_type = map_input_type(row_en[4], level=2)
                is_required = cell_str(row_en[6]).lower() == 'required'

                attribute, _ = TechnicalAttribute.objects.update_or_create(
                    external_id=external_id,
                    defaults={
                        'name_en': cell_str(row_en[3]),
                        'name_fr': cell_str(row_fr[3]),
                        'attribute_level': 2,
                        'attribute_type': attribute_type,
                        'unit': self._resolve_unit(row_en[5]),
                        'is_required': is_required,
                        'is_searchable': attribute_type == 'choice',
                        'is_comparable': attribute_type == 'number',
                    },
                )

                CategoryAttribute.objects.update_or_create(
                    category=category,
                    attribute=attribute,
                    defaults={
                        'sort_order': level_1_count + sort_order,
                        'is_required_for_publish': is_required,
                    },
                )

        return stats
