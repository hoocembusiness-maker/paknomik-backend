"""Désactive le Pak-dating jusqu'à Paknomik 2.0.

Non destructif : réversible via PlatformSetting ou migration inverse.
"""

from django.db import migrations


def disable_pak_dating(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.update_or_create(
        key="pak_dating_enabled",
        defaults={
            "value": "false",
            "label": "Pak-dating avec consentement acheteur",
        },
    )


def enable_pak_dating(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PlatformSetting.objects.filter(key="pak_dating_enabled").update(value="true")


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0069_search_term_map"),
    ]

    operations = [
        migrations.RunPython(disable_pak_dating, enable_pak_dating),
    ]
