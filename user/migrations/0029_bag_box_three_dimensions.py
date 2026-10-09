"""
Django database migration `0029_bag_box_three_dimensions`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def promote_three_dimensions(apps, schema_editor):
    TechnicalAttribute = apps.get_model("user", "TechnicalAttribute")
    CategoryAttribute = apps.get_model("user", "CategoryAttribute")
    Category = apps.get_model("user", "Category")

    # Paper / carrier bags — Width, Height (L1) + Soufflet (L2 → L1)
    TechnicalAttribute.objects.filter(id=725).update(attribute_level=1)
    sacs = Category.objects.filter(slug="sacs").first()
    if sacs:
        sort_map = {
            662: 3,  # Width / Largeur
            661: 4,  # Height / Hauteur
            725: 5,  # Soufflet (3rd dimension)
        }
        for attr_id, sort_order in sort_map.items():
            CategoryAttribute.objects.filter(
                category=sacs, attribute_id=attr_id
            ).update(sort_order=sort_order)

    # Folding cartons — L × W × H at level 1
    for attr_id in (743, 744, 745):
        TechnicalAttribute.objects.filter(id=attr_id).update(attribute_level=1)

    boites = Category.objects.filter(slug="boites-carton").first()
    if boites:
        sort_map = {
            743: 10,  # Longueur
            744: 11,  # Largeur
            745: 12,  # Hauteur
        }
        for attr_id, sort_order in sort_map.items():
            CategoryAttribute.objects.filter(
                category=boites, attribute_id=attr_id
            ).update(sort_order=sort_order)

    # Corrugated — max L × W × H at level 1
    for attr_id in (756, 757, 758):
        TechnicalAttribute.objects.filter(id=attr_id).update(attribute_level=1)

    carton = Category.objects.filter(slug="carton-ondule").first()
    if carton:
        sort_map = {
            756: 5,
            757: 6,
            758: 7,
        }
        for attr_id, sort_order in sort_map.items():
            CategoryAttribute.objects.filter(
                category=carton, attribute_id=attr_id
            ).update(sort_order=sort_order)


def revert_three_dimensions(apps, schema_editor):
    TechnicalAttribute = apps.get_model("user", "TechnicalAttribute")

    TechnicalAttribute.objects.filter(id=725).update(attribute_level=2)
    for attr_id in (743, 744, 745, 756, 757, 758):
        TechnicalAttribute.objects.filter(id=attr_id).update(attribute_level=2)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0028_product_custom_keywords"),
    ]

    operations = [
        migrations.RunPython(promote_three_dimensions, revert_three_dimensions),
    ]
