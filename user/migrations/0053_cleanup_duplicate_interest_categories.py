"""
Django database migration `0053_cleanup_duplicate_interest_categories`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def apply_cleanup(apps, schema_editor):
    from user.catalog_duplicate_category_cleanup import (
        cleanup_duplicate_interest_categories,
    )

    Category = apps.get_model("user", "Category")
    Product = apps.get_model("user", "Product")
    Supplier = apps.get_model("user", "Supplier")
    TenderCall = apps.get_model("user", "TenderCall")

    cleanup_duplicate_interest_categories(
        Category=Category,
        Product=Product,
        Supplier=Supplier,
        SourcingRequest=TenderCall,
        dry_run=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0052_move_mousse_under_emballage_industriel"),
    ]

    operations = [
        migrations.RunPython(apply_cleanup, migrations.RunPython.noop),
    ]
