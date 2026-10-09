"""Seed roles, plan entitlements, platform settings and the Pako catalog.

Non-destructive: every helper is idempotent (update_or_create / get_or_create)
and the reverse operation is a no-op, so rolling back never deletes data.
"""

from django.db import migrations


def seed_forward(apps, schema_editor):
    from user.entitlements import seed_entitlements
    from user.pakdinar import seed_pakdinar_catalog
    from user.platform_config import seed_settings
    from user.rbac import seed_roles

    seed_roles()
    seed_entitlements()
    seed_settings()
    # Re-run so the new commercial.pak_dating tariff row exists.
    seed_pakdinar_catalog()


def backfill_wallets(apps, schema_editor):
    """Give existing transactions a reference and a DA equivalent."""
    from decimal import Decimal
    import uuid

    PakdinarLedger = apps.get_model("user", "PakdinarLedger")
    PakdinarWallet = apps.get_model("user", "PakdinarWallet")

    for entry in PakdinarLedger.objects.filter(reference=""):
        entry.reference = f"PKO-{uuid.uuid4().hex[:12].upper()}"
        entry.amount_dzd = abs(entry.amount) * Decimal("100")
        entry.entry_type = "spend" if entry.amount < 0 else "purchase"
        entry.save(update_fields=["reference", "amount_dzd", "entry_type"])

    for wallet in PakdinarWallet.objects.all():
        spent = sum(
            abs(e.amount)
            for e in wallet.entries.all()
            if e.amount < 0
        )
        credited = sum(e.amount for e in wallet.entries.all() if e.amount > 0)
        wallet.spent_total = spent
        wallet.purchased_total = credited
        wallet.save(update_fields=["spent_total", "purchased_total"])


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0060_auditlog_campaign_consentrecord_crmactivity_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
        migrations.RunPython(backfill_wallets, migrations.RunPython.noop),
    ]
