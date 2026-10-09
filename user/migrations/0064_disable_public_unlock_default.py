"""Désactive l'accès payant aux devis publics tant que le tarif n'est pas validé.

Non destructif : inverse réactive le flag. Aucune donnée CRM n'est touchée.
"""

from django.db import migrations


def disable_unlock(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.update_or_create(
        key="public_quote_unlock_enabled",
        defaults={
            "value": "false",
            "label": "Accès payant d'un fournisseur non sélectionné à une demande publique (à valider)",
        },
    )


def enable_unlock(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.filter(key="public_quote_unlock_enabled").update(
        value="true"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0063_marketreport"),
    ]

    operations = [
        migrations.RunPython(disable_unlock, enable_unlock),
    ]
