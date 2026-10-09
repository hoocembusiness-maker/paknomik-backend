"""
Import SearchTag rows from paknomik_mots_cles_produits_exhaustif.csv.

Usage:
  python manage.py import_search_tags_exhaustif --dry-run
  python manage.py import_search_tags_exhaustif
  python manage.py import_search_tags_exhaustif --clear
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user.models import SearchTag
from user.search_vocabulary import invalidate_vocabulary_cache


def cell_str(value):
    if value is None:
        return ''
    return str(value).strip()


class Command(BaseCommand):
    help = 'Import search tags from paknomik_mots_cles_produits_exhaustif.csv'

    def add_arguments(self, parser):
        default_file = (
            Path(__file__).resolve().parents[2]
            / 'paknomik_mots_cles_produits_exhaustif.csv'
        )
        parser.add_argument(
            '--file',
            type=str,
            default=str(default_file),
            help='Path to the exhaustive keywords CSV',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to the database',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing search tags before import',
        )

    def handle(self, *args, **options):
        file_path = Path(options['file'])
        dry_run = options['dry_run']
        clear = options['clear']

        if not file_path.exists():
            raise CommandError(f'CSV file not found: {file_path}')

        with open(file_path, encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle, delimiter=';'))

        if not rows:
            raise CommandError('CSV file is empty')

        if clear and not dry_run:
            SearchTag.objects.all().delete()
            self.stdout.write(self.style.WARNING('Cleared existing search tags'))

        created = 0
        updated = 0

        with transaction.atomic():
            for row in rows:
                tag = cell_str(row.get('mot_cle_en'))
                if not tag:
                    continue

                defaults = {
                    'tag_fr': cell_str(row.get('mot_cle_fr')),
                    'tag_ar': cell_str(row.get('mot_cle_ar')),
                    'tag_group': cell_str(row.get('type_source')),
                    'industry_name': cell_str(row.get('segments_concernes')),
                    'sub_category': cell_str(row.get('cible')),
                    'search_weight': int(cell_str(row.get('poids')) or 0),
                }

                if dry_run:
                    created += 1
                    continue

                _, was_created = SearchTag.objects.update_or_create(
                    tag=tag,
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS('Import summary:'))
        self.stdout.write(f'  rows processed: {len(rows)}')
        self.stdout.write(f'  created: {created}')
        self.stdout.write(f'  updated: {updated}')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes saved'))
        else:
            invalidate_vocabulary_cache()
