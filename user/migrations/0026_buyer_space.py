"""
Django database migration `0026_buyer_space`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0025_message"),
    ]

    operations = [
        migrations.CreateModel(
            name="BuyerSearchHistory",
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
                ("query", models.CharField(max_length=255)),
                ("rating", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "buyer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_history",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Buyer search history",
                "verbose_name_plural": "Buyer search history",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="BuyerFavoriteSupplier",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "buyer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favorite_suppliers",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favorited_by",
                        to="user.supplier",
                    ),
                ),
            ],
            options={
                "verbose_name": "Buyer favorite supplier",
                "verbose_name_plural": "Buyer favorite suppliers",
                "ordering": ["-created_at"],
                "unique_together": {("buyer", "supplier")},
            },
        ),
    ]
