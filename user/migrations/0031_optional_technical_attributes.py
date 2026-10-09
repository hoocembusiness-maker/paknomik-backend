"""
Django database migration `0031_optional_technical_attributes`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def clear_required_flags(apps, schema_editor):
    TechnicalAttribute = apps.get_model("user", "TechnicalAttribute")
    CategoryAttribute = apps.get_model("user", "CategoryAttribute")
    TechnicalAttribute.objects.all().update(is_required=False)
    CategoryAttribute.objects.all().update(is_required_for_publish=False)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0030_supplier_score"),
    ]

    operations = [
        migrations.RunPython(clear_required_flags, migrations.RunPython.noop),
    ]
