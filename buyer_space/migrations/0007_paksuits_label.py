from django.db import migrations


def rename_label(apps, schema_editor):
    ServiceAvailability = apps.get_model("buyer_space", "ServiceAvailability")
    ServiceAvailability.objects.filter(service_type="PAKSUITES", label="PakSuites").update(label="PakSuits")


def restore_label(apps, schema_editor):
    ServiceAvailability = apps.get_model("buyer_space", "ServiceAvailability")
    ServiceAvailability.objects.filter(service_type="PAKSUITES", label="PakSuits").update(label="PakSuites")


class Migration(migrations.Migration):

    dependencies = [
        ("buyer_space", "0006_suite_component_visuals"),
    ]

    operations = [
        migrations.RunPython(rename_label, restore_label),
    ]
