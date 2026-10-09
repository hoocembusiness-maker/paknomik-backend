"""
Django database migration `0033_merge_carton_categories`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def merge_carton_categories_forward(apps, schema_editor):
    from user.catalog_carton_merge import merge_carton_categories

    Category = apps.get_model("user", "Category")
    CategoryAttribute = apps.get_model("user", "CategoryAttribute")
    AttributeOption = apps.get_model("user", "AttributeOption")
    Product = apps.get_model("user", "Product")
    Supplier = apps.get_model("user", "Supplier")
    TenderCall = apps.get_model("user", "TenderCall")

    corrugated = Category.objects.filter(
        slug="carton-ondule", parent__isnull=True
    ).first()
    if not corrugated:
        compact = Category.objects.filter(
            slug="carton-et-derives", parent__isnull=True
        ).first()
        if compact:
            return
        compact = Category.objects.filter(
            slug="boites-carton", parent__isnull=True
        ).first()
        if not compact:
            return

    merge_carton_categories(
        Category=Category,
        CategoryAttribute=CategoryAttribute,
        AttributeOption=AttributeOption,
        Product=Product,
        Supplier=Supplier,
        SourcingRequest=TenderCall,
        dry_run=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0032_product_target_markets"),
    ]

    operations = [
        migrations.RunPython(
            merge_carton_categories_forward,
            migrations.RunPython.noop,
        ),
    ]
