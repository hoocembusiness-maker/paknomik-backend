"""Add Boîte compartimentée type, layout filter, and Pro Box dimensions."""

from django.db import migrations


def seed_compartiment(apps, schema_editor):
    from user.boite_compartiment import seed_boite_compartiment

    seed_boite_compartiment()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0137_catalog_taxonomy"),
    ]

    operations = [
        migrations.RunPython(seed_compartiment, migrations.RunPython.noop),
    ]
