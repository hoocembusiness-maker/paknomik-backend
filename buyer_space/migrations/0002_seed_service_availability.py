from django.db import migrations


SERVICES = (
    ("DESIGN", "Paknoma Design"),
    ("ANALYTICS", "Paklytik"),
    ("PAKSUITES", "PakSuites"),
    ("PAKMATH", "Pakmath"),
)


def seed_availability(apps, schema_editor):
    ServiceAvailability = apps.get_model("buyer_space", "ServiceAvailability")
    for service_type, label in SERVICES:
        ServiceAvailability.objects.get_or_create(
            service_type=service_type,
            defaults={
                "label": label,
                "state": "available_on_request",
                "message": "",
            },
        )


def unseed_availability(apps, schema_editor):
    ServiceAvailability = apps.get_model("buyer_space", "ServiceAvailability")
    ServiceAvailability.objects.filter(
        service_type__in=[service_type for service_type, _label in SERVICES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("buyer_space", "0001_buyer_space_links"),
    ]

    operations = [
        migrations.RunPython(seed_availability, unseed_availability),
    ]
