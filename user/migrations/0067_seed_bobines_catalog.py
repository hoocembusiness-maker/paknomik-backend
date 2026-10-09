"""Seed Bobines category and attributes (idempotent)."""

from django.db import migrations


def seed_bobines(apps, schema_editor):
    from user.bobines_seed import apply_bobines_catalog

    apply_bobines_catalog(dry_run=False)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0066_backfill_crm_from_commerce"),
    ]

    operations = [
        migrations.RunPython(seed_bobines, migrations.RunPython.noop),
    ]
