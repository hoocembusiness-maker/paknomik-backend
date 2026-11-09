"""Ensure Matière, Dimensions, Fermeture on every packaging subcategory."""

from django.db import migrations


def seed_cores(apps, schema_editor):
    from user.core_packaging_attributes import seed_core_packaging_attributes

    seed_core_packaging_attributes()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0135_supplier_brand_button_color"),
    ]

    operations = [
        migrations.RunPython(seed_cores, migrations.RunPython.noop),
    ]
