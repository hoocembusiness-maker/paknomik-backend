"""
Django database migration `0036_supplier_settings`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0035_remove_flexpak_mm_dimensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("product_card_style", models.JSONField(blank=True, default=dict)),
                ("faq_items", models.JSONField(blank=True, default=list)),
                ("listing_form_fields", models.JSONField(blank=True, default=list)),
                ("listing_card_fields", models.JSONField(blank=True, default=list)),
                ("proposal_template", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "supplier",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="settings",
                        to="user.supplier",
                    ),
                ),
            ],
            options={
                "verbose_name": "Supplier settings",
                "verbose_name_plural": "Supplier settings",
            },
        ),
    ]
