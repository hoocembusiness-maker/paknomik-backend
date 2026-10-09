"""
Django database migration `0049_remove_gobelets_barquettes_subcategories`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def apply_removal(apps, schema_editor):
    from user.catalog_remove_gobelets_barquettes import (
        remove_gobelets_barquettes_subcategories,
    )

    remove_gobelets_barquettes_subcategories(dry_run=False)


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0048_move_gobelets_barquettes_to_restauration"),
    ]

    operations = [
        migrations.RunPython(apply_removal, migrations.RunPython.noop),
    ]
