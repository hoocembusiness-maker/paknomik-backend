from django.db import migrations


def apply_price_list(apps, schema_editor):
    from user.pragmadinar import CATALOG_SERVICES, RETIRED_SERVICE_CODES

    Service = apps.get_model("user", "PragmadinarService")
    fields = {field.name for field in Service._meta.get_fields()}
    for row in CATALOG_SERVICES:
        defaults = {key: value for key, value in row.items() if key in fields and key != "code"}
        Service.objects.update_or_create(code=row["code"], defaults=defaults)
    Service.objects.filter(code__in=RETIRED_SERVICE_CODES).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0099_pragmadinar_service_rank_unit"),
    ]

    operations = [
        migrations.RunPython(apply_price_list, migrations.RunPython.noop),
    ]
