"""Set level-1 and level-2 attributes for bottles, cans, industrial packs and labels."""

from django.db import migrations


def apply_attributes(apps, schema_editor):
    from user.catalog_family_attributes import apply_container_and_label_attributes

    apply_container_and_label_attributes()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0141_supplier_technical_attribute_proposals"),
    ]

    operations = [
        migrations.RunPython(apply_attributes, migrations.RunPython.noop),
    ]
