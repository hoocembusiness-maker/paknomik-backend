"""Catalogue : photo produit supplémentaire (4e et suivantes)."""

from decimal import Decimal

from django.db import migrations


def add_extra_photo_service(apps, schema_editor):
    PakdinarService = apps.get_model("user", "PakdinarService")
    PakdinarService.objects.update_or_create(
        code="commercial.product_extra_photo",
        defaults={
            "service_class": "commercial",
            "label": "Photo produit supplémentaire (4e et suivantes)",
            "cost_pakdinar": Decimal("0.1"),
            "unit": "one_shot",
            "unit_quantity": 1,
            "business_access": "pakdinar",
            "genereux_access": "pakdinar",
            "genereux_quota": None,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0080_normalize_legacy_welcome_grant500"),
    ]

    operations = [
        migrations.RunPython(add_extra_photo_service, migrations.RunPython.noop),
    ]
