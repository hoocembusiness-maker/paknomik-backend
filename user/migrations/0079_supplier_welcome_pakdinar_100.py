"""Crédit de bienvenue : 100 Pakdinar (paramètre plateforme + batch fournisseurs)."""

from django.db import migrations


def apply_welcome_grants(apps, schema_editor):
    from user.platform_config import seed_settings, supplier_welcome_pakdinar
    from user.models import Supplier
    from user.pakdinar import grant_supplier_welcome_pakdinar

    seed_settings()
    amount = supplier_welcome_pakdinar()
    if amount <= 0:
        return
    from user.crm import fields_missing_from_db

    suppliers = Supplier.objects.filter(is_active=True).defer(*fields_missing_from_db(Supplier))
    for supplier in suppliers.iterator():
        grant_supplier_welcome_pakdinar(supplier)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0078_entitlements_prepaid_intel"),
    ]

    operations = [
        migrations.RunPython(apply_welcome_grants, migrations.RunPython.noop),
    ]
