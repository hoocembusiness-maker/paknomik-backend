"""
Django database migration `0037_supplier_settings_extended`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0036_supplier_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="suppliersettings",
            name="listing_rules",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="suppliersettings",
            name="proposal_blocks",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="suppliersettings",
            name="page_design",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
