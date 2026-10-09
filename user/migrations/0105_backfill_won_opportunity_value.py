"""Fill the value of won CRM opportunities from their linked order when it is missing."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import migrations
from django.db.models import Q


def _order_amount(order):
    snapshot = order.commercial_snapshot or {}
    total = (snapshot.get("totals") or {}).get("totalAmount")
    if total not in (None, ""):
        try:
            return Decimal(str(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            pass
    unit_price = order.unit_price or Decimal("0")
    if unit_price > 0 and order.quantity:
        return (unit_price * order.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return None


def backfill_won_values(apps, schema_editor):
    Opportunity = apps.get_model("user", "Opportunity")
    qs = (
        Opportunity.objects.filter(stage="won", order__isnull=False)
        .filter(Q(estimated_value__isnull=True) | Q(estimated_value=0))
        .select_related("order")
    )
    for opportunity in qs.iterator():
        amount = _order_amount(opportunity.order)
        if amount:
            opportunity.estimated_value = amount
            opportunity.save(update_fields=["estimated_value"])


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0104_campaign_objectives_conversation_banner"),
    ]

    operations = [
        migrations.RunPython(backfill_won_values, migrations.RunPython.noop),
    ]
