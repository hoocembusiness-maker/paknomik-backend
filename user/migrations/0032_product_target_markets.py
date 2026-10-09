"""
Django database migration `0032_product_target_markets`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0031_optional_technical_attributes"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="target_markets",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Commercial segments this product targets (restaurant, cafe, pharma, etc.).",
            ),
        ),
    ]
