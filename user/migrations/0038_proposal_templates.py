"""
Django database migration `0038_proposal_templates`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0037_supplier_settings_extended"),
    ]

    operations = [
        migrations.AddField(
            model_name="suppliersettings",
            name="proposal_templates",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="quoteresponse",
            name="proposal_blocks",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="quoteresponse",
            name="proposal_template_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="quoteresponse",
            name="proposal_template_name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
    ]
