"""Align CRM pipeline with orders already marked won/lost."""

from django.db import migrations


def backfill_order_outcomes(apps, schema_editor):
    from user.models import Order

    from user import crm

    qs = Order.objects.filter(status__in=["won", "lost"]).select_related(
        "supplier", "product", "buyer"
    )
    for order in qs.defer(*crm.order_backfill_deferred_fields()).iterator():
        outcome = "won" if order.status == "won" else "lost"
        try:
            crm.sync_order_outcome(order, outcome=outcome)
        except Exception:
            continue


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0074_order_quote_response"),
    ]

    operations = [
        migrations.RunPython(backfill_order_outcomes, migrations.RunPython.noop),
    ]
