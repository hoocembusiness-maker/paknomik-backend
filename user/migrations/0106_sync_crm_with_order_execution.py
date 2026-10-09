"""Move CRM deals of confirmed / in-production / shipped / delivered / completed orders to won."""

from django.db import migrations


def sync_execution(apps, schema_editor):
    from user.models import Order

    from user import crm

    qs = Order.objects.filter(status__in=crm.ORDER_STATUSES_WON).select_related(
        "supplier", "product", "buyer"
    )
    for order in qs.defer(*crm.order_backfill_deferred_fields()).iterator():
        try:
            crm.sync_order_execution(order)
        except Exception:
            continue


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0105_backfill_won_opportunity_value"),
    ]

    operations = [
        migrations.RunPython(sync_execution, migrations.RunPython.noop),
    ]
