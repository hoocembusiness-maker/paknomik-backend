"""
Django database migration `0035_remove_flexpak_mm_dimensions`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def remove_flexpak_mm_dimensions_forward(apps, schema_editor):
    from user.management.commands.seed_flexpak_dimensions import (
        REMOVED_MM_EXTERNAL_IDS,
        SORT_AFTER_DIMENSIONS,
    )

    Category = apps.get_model("user", "Category")
    CategoryAttribute = apps.get_model("user", "CategoryAttribute")

    parent = Category.objects.filter(slug="flexpak", parent__isnull=True).first()
    if not parent:
        return

    CategoryAttribute.objects.filter(
        category=parent,
        attribute__external_id__in=REMOVED_MM_EXTERNAL_IDS,
    ).delete()

    for external_id, sort_order in SORT_AFTER_DIMENSIONS.items():
        CategoryAttribute.objects.filter(
            category=parent,
            attribute__external_id=external_id,
        ).update(sort_order=sort_order)


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0034_flexpak_replace_taille_with_dimensions"),
    ]

    operations = [
        migrations.RunPython(
            remove_flexpak_mm_dimensions_forward,
            migrations.RunPython.noop,
        ),
    ]
