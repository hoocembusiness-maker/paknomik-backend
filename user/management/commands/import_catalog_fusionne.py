"""
Import v2 catalog (categories, subcategories, attributes) from
paknomik_catalog_model_fusionne.xlsx.

Sheets used (by index):
  0  1_Catégories           -> Category (level 1)
  1  2_Sous_catégories      -> Category (level 2, with parent)
  4  5_Filtres_recherche    -> TechnicalAttribute Level 1
  5  6_Fiches_produit       -> TechnicalAttribute Level 2

Usage:
  python manage.py import_catalog_fusionne --dry-run
  python manage.py import_catalog_fusionne
  python manage.py import_catalog_fusionne --clear
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

SHEET_CATEGORIES = 0
SHEET_SUBCATEGORIES = 1
SHEET_LEVEL_1 = 4
SHEET_LEVEL_2 = 5


def cell_str(value):
    if value is None:
        return ''
    return str(value).strip()


def slug_to_name_en(slug):
    return cell_str(slug).replace('-', ' ').title()


def parse_options(raw):
    text = cell_str(raw)
    if not text:
        return []
    return [part.strip() for part in text.split('|') if part.strip()]


def map_input_type(raw, level):
    text = cell_str(raw).lower()
    if level == 1:
        if text in {'dropdown', 'liste', 'choice'}:
            return 'choice'
        return 'text'
    if text in {'number', 'nombre'}:
        return 'number'
    if text in {'boolean', 'booléen', 'booleen'}:
        return 'boolean'
    if text in {'choice', 'dropdown', 'liste'}:
        return 'choice'
    return 'text'


def category_external_id(category_id):
    return f'v2-{int(category_id):02d}'


def subcategory_external_id(subcategory_id):
    return f'v2-sub-{int(subcategory_id)}'


def attribute_external_id(category_id, level, sort_order):
    return f'v2-{int(category_id):02d}-L{level}-{sort_order:02d}'


def sheet_rows(workbook, index, header_row=0):
    sheet = workbook.worksheets[index]
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return [], []
    return rows[header_row], rows[header_row + 1 :]


class Command(BaseCommand):
    help = (
        'Import categories, subcategories, and attributes from '
        'paknomik_catalog_model_fusionne.xlsx'
    )

    def add_arguments(self, parser):
        default_file = (
            Path(__file__).resolve().parents[2]
            / 'paknomik_catalog_model_fusionne.xlsx'
        )
        parser.add_argument(
            '--file',
            type=str,
            default=str(default_file),
            help='Path to the fused catalog workbook',
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
        file_path = Path(options['file'])
        dry_run = options['dry_run']
        clear = options['clear']

        if not file_path.exists():
            raise CommandError(f'Excel file not found: {file_path}')

        workbook = openpyxl.load_workbook(file_path, data_only=True)
        stats = {}

        if clear and not dry_run:
            self._clear_v2_catalog()

        with transaction.atomic():
            category_map = self._import_categories(workbook, dry_run)
            stats['categories'] = len(category_map)
            stats['subcategories'] = self._import_subcategories(
                workbook, category_map, dry_run
            )
            stats['level_1'] = self._import_level_1_attributes(
                workbook, category_map, dry_run
            )
            stats['level_2'] = self._import_level_2_attributes(
                workbook, category_map, dry_run
            )

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS('Import summary:'))
        for key, value in stats.items():
            self.stdout.write(f'  {key}: {value}')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes saved'))

    def _clear_v2_catalog(self):
        AttributeOption.objects.filter(
            category_attribute__category__external_id__startswith='v2-'
        ).delete()
        CategoryAttribute.objects.filter(
            category__external_id__startswith='v2-'
        ).delete()
        TechnicalAttribute.objects.filter(external_id__startswith='v2-').delete()
        Category.objects.filter(external_id__startswith='v2-sub-').delete()
        self.stdout.write(self.style.WARNING('Cleared v2 subcategories and attributes'))

    def _existing_name_en(self, external_id):
        existing = Category.objects.filter(external_id=external_id).first()
        if existing and existing.name_en:
            return existing.name_en
        return ''

    def _import_categories(self, workbook, dry_run):
        _, rows = sheet_rows(workbook, SHEET_CATEGORIES, header_row=2)
        category_map = {}

        for row in rows:
            category_id = row[0]
            if category_id is None:
                continue

            external_id = category_external_id(category_id)
            slug = cell_str(row[1])
            name_fr = cell_str(row[2])
            name_en = self._existing_name_en(external_id) or slug_to_name_en(slug)

            defaults = {
                'slug': slug,
                'sort_order': int(category_id),
                'name_en': name_en,
                'name_fr': name_fr,
                'plain_description': cell_str(row[3]),
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

    def _import_subcategories(self, workbook, category_map, dry_run):
        _, rows = sheet_rows(workbook, SHEET_SUBCATEGORIES, header_row=0)
        count = 0

        for row in rows:
            sub_id = row[0]
            if sub_id is None:
                continue

            parent_id = row[3]
            if parent_id is None:
                continue

            slug = cell_str(row[1])
            name_fr = cell_str(row[2])
            external_id = subcategory_external_id(sub_id)

            defaults = {
                'slug': slug,
                'sort_order': int(sub_id),
                'name_en': slug_to_name_en(slug),
                'name_fr': name_fr,
                'level': 2,
            }

            if dry_run:
                count += 1
                continue

            parent = category_map.get(int(parent_id))
            if not parent:
                self.stdout.write(
                    self.style.WARNING(
                        f'Skipping subcategory {sub_id}: parent {parent_id} not found'
                    )
                )
                continue

            category, _ = Category.objects.update_or_create(
                external_id=external_id,
                defaults={**defaults, 'parent': parent},
            )
            count += 1

        return count

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

    def _import_level_1_attributes(self, workbook, category_map, dry_run):
        _, rows = sheet_rows(workbook, SHEET_LEVEL_1, header_row=2)
        grouped = self._group_rows_by_category(rows)
        stats = {'attributes': 0, 'links': 0, 'options': 0}

        for category_id, category_rows in grouped.items():
            for sort_order, row in enumerate(category_rows, start=1):
                stats['attributes'] += 1
                stats['links'] += 1
                options = parse_options(row[5])
                stats['options'] += len(options)

                if dry_run:
                    continue

                category = category_map[category_id]
                external_id = attribute_external_id(category_id, 1, sort_order)
                attribute_type = map_input_type(row[4], level=1)

                attribute, _ = TechnicalAttribute.objects.update_or_create(
                    external_id=external_id,
                    defaults={
                        'name_en': slug_to_name_en(row[2]) or cell_str(row[3]),
                        'name_fr': cell_str(row[3]),
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

                for opt_idx, option in enumerate(options, start=1):
                    AttributeOption.objects.update_or_create(
                        category_attribute=link,
                        value_en=option,
                        defaults={
                            'value_fr': option,
                            'sort_order': opt_idx,
                        },
                    )

        return stats

    def _import_level_2_attributes(self, workbook, category_map, dry_run):
        _, rows = sheet_rows(workbook, SHEET_LEVEL_2, header_row=2)
        grouped = self._group_rows_by_category(rows)
        level_1_counts = {
            category_id: len(category_rows)
            for category_id, category_rows in grouped.items()
        }
        stats = {'attributes': 0, 'links': 0}

        for category_id, category_rows in grouped.items():
            level_1_count = level_1_counts.get(category_id, 0)

            for sort_order, row in enumerate(category_rows, start=1):
                stats['attributes'] += 1
                stats['links'] += 1

                if dry_run:
                    continue

                category = category_map[category_id]
                external_id = attribute_external_id(category_id, 2, sort_order)
                attribute_type = map_input_type(row[4], level=2)
                required_raw = cell_str(row[6]).lower()
                is_required = required_raw in {'required', 'obligatoire'}

                attribute, _ = TechnicalAttribute.objects.update_or_create(
                    external_id=external_id,
                    defaults={
                        'name_en': slug_to_name_en(row[2]) or cell_str(row[3]),
                        'name_fr': cell_str(row[3]),
                        'attribute_level': 2,
                        'attribute_type': attribute_type,
                        'unit': self._resolve_unit(row[5]),
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
