"""Resynchronise le listing tarifaire officiel (grille Pakdinar 2026)."""

from django.db import migrations


def resync_catalog(apps, schema_editor):
    from user.pakdinar import seed_pakdinar_catalog

    seed_pakdinar_catalog()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0076_crm_free_for_all_suppliers"),
    ]

    operations = [
        migrations.RunPython(resync_catalog, migrations.RunPython.noop),
    ]
