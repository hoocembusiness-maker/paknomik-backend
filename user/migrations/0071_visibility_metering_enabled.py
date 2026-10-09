"""Active le comptage de consommation visibilité pour tous les fournisseurs."""

from django.db import migrations


def enable_metering(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.update_or_create(
        key="visibility_metering_enabled",
        defaults={
            "value": "true",
            "label": "Comptage mensuel des apparitions et visites (quotas / consommation Pakdinar)",
        },
    )


def disable_metering(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.filter(key="visibility_metering_enabled").update(value="false")


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0070_disable_pak_dating"),
    ]

    operations = [
        migrations.RunPython(enable_metering, disable_metering),
    ]
