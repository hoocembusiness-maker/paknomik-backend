from decimal import Decimal

from django.db import migrations


def drop_monthly_subscription(apps, schema_editor):
    PaknomikPlan = apps.get_model("user", "PaknomikPlan")
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PaknomikPlan.objects.filter(code="business").update(monthly_price_dzd=Decimal("0"))
    PlatformSetting.objects.filter(key="all_suppliers_business_until").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0085_pako_rate_200_dzd"),
    ]

    operations = [
        migrations.RunPython(drop_monthly_subscription, migrations.RunPython.noop),
    ]
