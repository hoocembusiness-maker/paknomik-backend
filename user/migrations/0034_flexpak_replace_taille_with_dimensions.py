"""
Django database migration `0034_flexpak_replace_taille_with_dimensions`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def apply_flexpak_dimensions_forward(apps, schema_editor):
    from user.management.commands.seed_flexpak_dimensions import apply_flexpak_dimensions

    Category = apps.get_model("user", "Category")
    CategoryAttribute = apps.get_model("user", "CategoryAttribute")
    TechnicalAttribute = apps.get_model("user", "TechnicalAttribute")
    Unit = apps.get_model("user", "Unit")

    parent = Category.objects.filter(slug="flexpak", parent__isnull=True).first()
    if not parent:
        return

    already = CategoryAttribute.objects.filter(
        category=parent,
        attribute__external_id="v4-flexpak-L1-longueur",
    ).exists()
    if already:
        return

    apply_flexpak_dimensions(
        Category=Category,
        CategoryAttribute=CategoryAttribute,
        TechnicalAttribute=TechnicalAttribute,
        Unit=Unit,
        dry_run=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0033_merge_carton_categories"),
    ]

    operations = [
        migrations.RunPython(
            apply_flexpak_dimensions_forward,
            migrations.RunPython.noop,
        ),
    ]
