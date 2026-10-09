"""
Django command: load categories and attributes from the Excel knowledge base.

Fills reference tables (categories, materials, search tags, etc.) used by
search and product forms.

Usage:
  python manage.py import_knowledge_base --dry-run
  python manage.py import_knowledge_base
  python manage.py import_knowledge_base --clear
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user.models import (
    Category,
    CategoryAttribute,
    Industry,
    Lexicon,
    Material,
    PackagingComponent,
    ProductPackagingMapping,
    SearchTag,
    TechnicalAttribute,
    Unit,
)

try:
    import openpyxl
except ImportError as exc:  # pragma: no cover
    raise CommandError(
        'openpyxl is required. Install it with: pip install openpyxl'
    ) from exc


def cell_str(value):
    if value is None:
        return ''
    return str(value).strip()


def is_null(value):
    if value is None:
        return True
    text = str(value).strip().upper()
    return text in {'', 'NULL', 'NONE', 'N/A', 'NA'}


def yes_no(value):
    return str(value).strip().lower() in {'yes', 'y', 'true', '1'}


def header_map(header_row):
    return {cell_str(name): index for index, name in enumerate(header_row) if name}


def row_dict(header_row, row):
    mapping = header_map(header_row)
    return {
        key: row[index] if index < len(row) else None
        for key, index in mapping.items()
    }


def category_depth(row_data):
    for level in range(4, 0, -1):
        value = row_data.get(f'Level_{level}')
        if not is_null(value):
            return level
    return 1


def attribute_type(raw):
    text = cell_str(raw).lower()
    if text == 'number':
        return 'number'
    if text == 'boolean':
        return 'boolean'
    if text in {'choice', 'select', 'dropdown'}:
        return 'choice'
    return 'text'


class Command(BaseCommand):
    help = 'Import knowledge-base data from tech_attr_paknomik.xlsx'

    def add_arguments(self, parser):
        default_file = (
            Path(__file__).resolve().parents[2] / 'tech_attr_paknomik.xlsx'
        )
        parser.add_argument(
            '--file',
            type=str,
            default=str(default_file),
            help='Path to the Excel workbook',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to the database',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing knowledge-base rows before import',
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
            self._clear_knowledge_base()

        with transaction.atomic():
            stats['units'] = self._import_units(workbook, dry_run)
            stats['industries'] = self._import_industries(workbook, dry_run)
            category_map = self._import_categories(workbook, dry_run)
            stats['categories'] = len(category_map)
            stats['materials'] = self._import_materials(workbook, dry_run)
            stats['attributes'] = self._import_technical_attributes(
                workbook, dry_run, category_map
            )
            stats['lexicon'] = self._import_lexicon(workbook, dry_run, category_map)
            stats['search_tags'] = self._import_search_tags(workbook, dry_run, category_map)
            stats['mappings'] = self._import_product_mappings(
                workbook, dry_run, category_map
            )
            stats['components'] = self._import_packaging_components(workbook, dry_run)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS('Import summary:'))
        for key, value in stats.items():
            self.stdout.write(f'  {key}: {value}')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes saved'))

    def _clear_knowledge_base(self):
        SearchTag.objects.all().delete()
        Lexicon.objects.all().delete()
        PackagingComponent.objects.all().delete()
        ProductPackagingMapping.objects.all().delete()
        CategoryAttribute.objects.all().delete()
        TechnicalAttribute.objects.all().delete()
        Category.objects.all().delete()
        Material.objects.all().delete()
        Industry.objects.all().delete()
        Unit.objects.all().delete()
        self.stdout.write(self.style.WARNING('Cleared knowledge-base tables'))

    def _sheet_rows(self, workbook, sheet_name):
        if sheet_name not in workbook.sheetnames:
            raise CommandError(f'Missing sheet: {sheet_name}')
        rows = list(workbook[sheet_name].iter_rows(values_only=True))
        if not rows:
            return [], []
        return rows[0], rows[1:]

    def _import_units(self, workbook, dry_run):
        header, rows = self._sheet_rows(workbook, 'Units Dictionary')
        count = 0
        for row in rows:
            data = row_dict(header, row)
            name = cell_str(data.get('Unit'))
            if not name:
                continue
            symbol = cell_str(data.get('Symbol'))
            if not dry_run:
                Unit.objects.update_or_create(
                    name=name,
                    defaults={'symbol': symbol},
                )
            count += 1
        return count

    def _import_industries(self, workbook, dry_run):
        header, rows = self._sheet_rows(workbook, 'Packaging Industries')
        count = 0
        for row in rows:
            data = row_dict(header, row)
            name = cell_str(data.get('Industry'))
            if not name:
                continue
            if not dry_run:
                Industry.objects.update_or_create(name=name)
            count += 1
        return count

    def _import_categories(self, workbook, dry_run):
        header, rows = self._sheet_rows(workbook, 'Packaging Taxonomy')
        pending_parents = []
        category_map = {}

        for row in rows:
            data = row_dict(header, row)
            external_id = cell_str(data.get('Category_ID'))
            if not external_id:
                continue

            defaults = {
                'level': category_depth(data),
                'name_en': cell_str(data.get('English_Name')) or external_id,
                'name_fr': cell_str(data.get('French_Name')),
                'name_ar': cell_str(data.get('Arabic_Name')),
                'scientific_description': cell_str(data.get('Scientific_Description')),
                'typical_industries': cell_str(data.get('Typical_Industries')),
                'typical_applications': cell_str(data.get('Typical_Applications')),
                'examples': cell_str(data.get('Examples')),
            }

            if dry_run:
                category_map[external_id] = external_id
            else:
                category, _ = Category.objects.update_or_create(
                    external_id=external_id,
                    defaults=defaults,
                )
                category_map[external_id] = category

            parent_raw = data.get('Parent_Category_ID')
            if not is_null(parent_raw):
                pending_parents.append((external_id, cell_str(parent_raw)))

        if not dry_run:
            for child_id, parent_id in pending_parents:
                child = category_map.get(child_id)
                parent = category_map.get(parent_id)
                if child and parent and child.parent_id != parent.id:
                    child.parent = parent
                    child.save(update_fields=['parent'])

        return category_map

    def _import_materials(self, workbook, dry_run):
        header, rows = self._sheet_rows(workbook, 'Packaging Materials')
        count = 0
        for row in rows:
            data = row_dict(header, row)
            external_id = cell_str(data.get('Material_ID'))
            name_en = cell_str(data.get('Material_Name_EN'))
            if not external_id and not name_en:
                continue
            defaults = {
                'name_en': name_en or external_id,
                'name_fr': cell_str(data.get('Material_Name_FR')),
                'name_ar': cell_str(data.get('Material_Name_AR')),
                'family': cell_str(data.get('Material_Family')),
            }
            if not dry_run:
                if external_id:
                    Material.objects.update_or_create(
                        external_id=external_id,
                        defaults=defaults,
                    )
                else:
                    Material.objects.update_or_create(
                        name_en=defaults['name_en'],
                        defaults=defaults,
                    )
            count += 1
        return count

    def _resolve_unit(self, raw):
        token = cell_str(raw)
        if not token:
            return None
        unit = Unit.objects.filter(symbol__iexact=token).first()
        if unit:
            return unit
        return Unit.objects.filter(name__iexact=token).first()

    def _import_technical_attributes(self, workbook, dry_run, category_map):
        header, rows = self._sheet_rows(workbook, 'Technical Attributes')
        attr_count = 0
        link_count = 0

        for row in rows:
            data = row_dict(header, row)
            external_id = cell_str(data.get('Attribute_ID'))
            category_id = cell_str(data.get('Category_ID'))
            if not external_id or not category_id:
                continue

            attr_defaults = {
                'name_en': cell_str(data.get('Attribute_Name_EN')) or external_id,
                'name_fr': cell_str(data.get('Attribute_Name_FR')),
                'name_ar': cell_str(data.get('Attribute_Name_AR')),
                'attribute_type': attribute_type(data.get('Attribute_Type')),
                'is_required': yes_no(data.get('Required')),
                'is_searchable': yes_no(data.get('Searchable')),
                'is_comparable': yes_no(data.get('Comparable')),
                'description': cell_str(data.get('Description')),
                'examples': cell_str(data.get('Examples')),
            }

            if dry_run:
                attr_count += 1
                link_count += 1
                continue

            unit = self._resolve_unit(data.get('Unit'))
            attribute, _ = TechnicalAttribute.objects.update_or_create(
                external_id=external_id,
                defaults={**attr_defaults, 'unit': unit},
            )
            attr_count += 1

            category = category_map.get(category_id)
            if isinstance(category, Category):
                _, created = CategoryAttribute.objects.get_or_create(
                    category=category,
                    attribute=attribute,
                )
                if created:
                    link_count += 1

        return {'attributes': attr_count, 'links': link_count}

    def _find_category(self, raw_name, category_map):
        name = cell_str(raw_name)
        if not name:
            return None
        for category in category_map.values():
            if isinstance(category, Category) and category.name_en.lower() == name.lower():
                return category
        return Category.objects.filter(name_en__iexact=name).first()

    def _find_industry(self, raw_name):
        name = cell_str(raw_name)
        if not name:
            return None
        first = name.split(',')[0].strip()
        industry = Industry.objects.filter(name__iexact=first).first()
        if industry:
            return industry
        return Industry.objects.filter(name__icontains=first).first()

    def _import_lexicon(self, workbook, dry_run, category_map):
        header, rows = self._sheet_rows(workbook, 'Packaging Lexicon')
        count = 0
        for row in rows:
            data = row_dict(header, row)
            keyword = cell_str(data.get('Keyword'))
            if not keyword:
                continue

            if not dry_run:
                Lexicon.objects.create(
                    keyword=keyword,
                    french=cell_str(data.get('French')),
                    arabic=cell_str(data.get('Arabic')),
                    category_name=cell_str(data.get('Category')),
                    industry_name=cell_str(data.get('Industry')),
                    related_product=cell_str(data.get('Related_Product')),
                    synonyms=cell_str(data.get('Synonyms')),
                    alternative_names=cell_str(data.get('Alternative_Names')),
                    search_weight=int(data.get('Search_Weight') or 0),
                    notes=cell_str(data.get('Notes')),
                )
            count += 1
        return count

    def _import_search_tags(self, workbook, dry_run, category_map):
        header, rows = self._sheet_rows(workbook, 'Search Tags')
        count = 0
        for row in rows:
            data = row_dict(header, row)
            tag = cell_str(data.get('Tag'))
            if not tag:
                continue

            if not dry_run:
                SearchTag.objects.create(
                    tag=tag,
                    tag_fr=cell_str(data.get('French')),
                    tag_group=cell_str(data.get('Tag_Group')),
                    industry_name=cell_str(data.get('Industry')),
                    sub_category=cell_str(data.get('Sub_Category')),
                    search_weight=int(data.get('Search_Weight') or 0),
                    synonyms=cell_str(data.get('Synonyms')),
                )
            count += 1
        return count

    def _import_product_mappings(self, workbook, dry_run, category_map):
        header, rows = self._sheet_rows(workbook, 'Product Packaging Mapping')
        count = 0
        for row in rows:
            data = row_dict(header, row)
            final_product = cell_str(data.get('Final_Product'))
            if not final_product:
                continue

            if not dry_run:
                industry = self._find_industry(data.get('Industry'))
                ProductPackagingMapping.objects.create(
                    final_product=final_product,
                    industry=industry,
                    industry_name_raw=cell_str(data.get('Industry')),
                    primary_packaging=cell_str(data.get('Primary_Packaging')),
                    secondary_packaging=cell_str(data.get('Secondary_Packaging')),
                    tertiary_packaging=cell_str(data.get('Tertiary_Packaging')),
                    transport_packaging=cell_str(data.get('Transport_Packaging')),
                    protective_packaging=cell_str(data.get('Protective_Packaging')),
                    possible_materials=cell_str(data.get('Possible_Materials')),
                    typical_labels=cell_str(data.get('Typical_Labels')),
                    typical_closures=cell_str(data.get('Typical_Closures')),
                    examples=cell_str(data.get('Examples')),
                )
            count += 1
        return count

    def _import_packaging_components(self, workbook, dry_run):
        header, rows = self._sheet_rows(workbook, 'Packaging Components')
        count = 0
        for row in rows:
            data = row_dict(header, row)
            product_category = cell_str(data.get('Product_Category'))
            component_name = cell_str(data.get('Component_Name'))
            if not product_category or not component_name:
                continue

            if not dry_run:
                PackagingComponent.objects.create(
                    product_category=product_category,
                    component_level=cell_str(data.get('Component_Level')),
                    component_name=component_name,
                    material=cell_str(data.get('Material')),
                    description=cell_str(data.get('Description')),
                    role=cell_str(data.get('Role')),
                    typical_spec=cell_str(data.get('Typical_Spec')),
                    examples=cell_str(data.get('Examples')),
                )
            count += 1
        return count
