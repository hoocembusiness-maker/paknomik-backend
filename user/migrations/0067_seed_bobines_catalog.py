"""Seed Bobines category and attributes (idempotent)."""

from django.db import migrations, models


def seed_bobines(apps, schema_editor):
    from user.bobines_seed import apply_bobines_catalog

    apply_bobines_catalog(dry_run=False)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0066_backfill_crm_from_commerce"),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='external_id',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='technicalattribute',
            name='external_id',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(seed_bobines, migrations.RunPython.noop),
    ]
