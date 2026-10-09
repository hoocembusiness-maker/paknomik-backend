"""Offre Business à tous les fournisseurs jusqu'au 31/03/2027.

Non destructif : le plan stocké (Généreux / Business) n'est pas modifié.
L'override est lu dans les entitlements ; après la date, chacun reprend
son plan réel. Inverse : vide le réglage.
"""

from django.db import migrations


def enable_promo(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.update_or_create(
        key="all_suppliers_business_until",
        defaults={
            "value": "2027-03-31",
            "label": (
                "Tous les fournisseurs sont traités comme Business jusqu'à "
                "cette date (AAAA-MM-JJ). Vider pour désactiver."
            ),
        },
    )


def disable_promo(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.filter(key="all_suppliers_business_until").update(value="")


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0064_disable_public_unlock_default"),
    ]

    operations = [
        migrations.RunPython(enable_promo, disable_promo),
    ]
