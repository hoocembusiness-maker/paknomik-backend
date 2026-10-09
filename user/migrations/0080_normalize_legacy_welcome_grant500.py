"""Align legacy 500 Pakdinar welcome grants with the 100 Pakdinar policy."""

from django.db import migrations


def normalize_all_legacy_welcome_grants(apps, schema_editor):
    from user.models import Supplier
    from user.pakdinar import normalize_legacy_welcome_grant

    from user.crm import fields_missing_from_db

    for supplier in Supplier.objects.defer(*fields_missing_from_db(Supplier)).iterator():
        normalize_legacy_welcome_grant(supplier)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0079_supplier_welcome_pakdinar_100"),
    ]

    operations = [
        migrations.RunPython(
            normalize_all_legacy_welcome_grants,
            migrations.RunPython.noop,
        ),
    ]
