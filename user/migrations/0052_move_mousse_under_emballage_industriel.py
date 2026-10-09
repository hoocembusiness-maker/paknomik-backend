"""
Django database migration `0052_move_mousse_under_emballage_industriel`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def apply_merge(apps, schema_editor):
    from user.catalog_mousse_merge import move_mousse_under_emballage_industriel

    move_mousse_under_emballage_industriel(dry_run=False)


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0051_supplier_brand_neutral_color"),
    ]

    operations = [
        migrations.RunPython(apply_merge, migrations.RunPython.noop),
    ]
