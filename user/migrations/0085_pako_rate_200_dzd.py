"""1 Pako = 200 DA (no expiry): align the setting and the DZD equivalent of the ledger.

Entries recorded while the rate was still 100 DA displayed a wrong DZD value
(e.g. the 100 Pakdinar welcome credit showed 10 000 DA instead of 20 000 DA).
Pako amounts and balances are untouched; only `rate_dzd` / `amount_dzd` change.
"""

from decimal import Decimal

from django.db import migrations

RATE = Decimal("200")
LEGACY_RATE = "100"


def apply_rate(apps, schema_editor):
    PlatformSetting = apps.get_model("user", "PlatformSetting")
    PakdinarLedger = apps.get_model("user", "PakdinarLedger")

    PlatformSetting.objects.filter(key="pako_rate_dzd", value__in=["", LEGACY_RATE]).update(
        value=str(RATE)
    )

    for entry in PakdinarLedger.objects.exclude(rate_dzd=RATE).iterator():
        entry.rate_dzd = RATE
        entry.amount_dzd = (abs(entry.amount) * RATE).quantize(Decimal("0.01"))
        entry.save(update_fields=["rate_dzd", "amount_dzd"])


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0084_quote_engagement_public_joined"),
    ]

    operations = [
        migrations.RunPython(apply_rate, migrations.RunPython.noop),
    ]
