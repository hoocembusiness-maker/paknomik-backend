"""Réinjecte commandes et devis boutique existants dans le CRM.

Non destructif : sync_from_order / sync_from_rfq sont idempotents.
Le plan stocké n'est pas modifié.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    from user.crm import backfill_from_existing_commerce

    backfill_from_existing_commerce()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0065_all_suppliers_business_until"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
