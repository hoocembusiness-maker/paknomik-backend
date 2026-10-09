"""
Django database migration `0048_move_gobelets_barquettes_to_restauration`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def apply_merge(apps, schema_editor):
    from user.catalog_restauration_merge import move_barquettes_gobelets_under_restauration

    move_barquettes_gobelets_under_restauration(dry_run=False)


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0047_user_orders_notifications_seen_at"),
    ]

    operations = [
        migrations.RunPython(apply_merge, migrations.RunPython.noop),
    ]
