from decimal import Decimal, InvalidOperation

from django.db import migrations, models
import django.db.models.deletion


def split_existing_balances(apps, schema_editor):
    Wallet = apps.get_model("user", "PragmadinarWallet")
    Ledger = apps.get_model("user", "PragmadinarLedger")
    Service = apps.get_model("user", "PragmadinarService")
    Setting = apps.get_model("user", "PlatformSetting")
    database = schema_editor.connection.alias

    setting = Setting.objects.using(database).filter(key="pragmadinar_rate_dzd").first()
    try:
        rate = Decimal(setting.value) if setting and setting.value else Decimal("200")
        if rate <= 0:
            rate = Decimal("200")
    except (InvalidOperation, TypeError):
        rate = Decimal("200")

    for service in Service.objects.using(database).all().iterator():
        if service.code.startswith("intel."):
            category = "analytics"
        elif service.code.startswith("promo.") or service.code in {
            "commercial.category_leads",
            "commercial.pak_dating",
        }:
            category = "promotion"
        else:
            category = "visibility"
        Service.objects.using(database).filter(pk=service.pk).update(
            wallet_category=category
        )

    categories = ("visibility", "analytics", "promotion")
    for legacy in Wallet.objects.using(database).filter(category="legacy").iterator():
        available = max(legacy.balance - legacy.held_total, Decimal("0"))
        share = (available / Decimal("3")).quantize(Decimal("0.01"))
        assigned = Decimal("0")

        for index, category in enumerate(categories):
            amount = available - assigned if index == len(categories) - 1 else share
            assigned += amount
            functional_wallet, _ = Wallet.objects.using(database).get_or_create(
                supplier_id=legacy.supplier_id,
                category=category,
                defaults={
                    "balance": Decimal("0"),
                    "granted_total": Decimal("0"),
                    "purchased_total": Decimal("0"),
                    "spent_total": Decimal("0"),
                    "held_total": Decimal("0"),
                },
            )
            if amount <= 0:
                continue

            ref = f"MIG-{legacy.pk}-{category}"[:64]
            Ledger.objects.using(database).create(
                wallet_id=legacy.pk,
                entry_type="transfer",
                status="posted",
                amount=-amount,
                rate_dzd=rate,
                amount_dzd=amount * rate,
                reference=ref,
                idempotency_key=f"migration:{legacy.pk}:out:{category}",
                reason=f"Répartition initiale vers {category}",
                ref_type="wallet_transfer",
                ref_id=functional_wallet.pk,
                refundable=False,
            )
            Ledger.objects.using(database).create(
                wallet_id=functional_wallet.pk,
                entry_type="transfer",
                status="posted",
                amount=amount,
                rate_dzd=rate,
                amount_dzd=amount * rate,
                reference=ref,
                idempotency_key=f"migration:{legacy.pk}:in:{category}",
                reason="Répartition initiale du solde existant",
                ref_type="wallet_transfer",
                ref_id=legacy.pk,
                refundable=False,
            )
            functional_wallet.balance += amount
            functional_wallet.save(update_fields=["balance"])

        legacy.balance -= available
        legacy.save(update_fields=["balance"])


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0095_rfq_in_review_notification"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="strategy_tool_visits",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="supplier",
            name="strategy_tool_last_visit_token",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="supplier",
            name="strategy_reminder_shown_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pragmadinarservice",
            name="wallet_category",
            field=models.CharField(
                choices=[
                    ("visibility", "Visibilité"),
                    ("analytics", "Analytics"),
                    ("promotion", "Promotion"),
                ],
                default="visibility",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="pragmadinarwallet",
            name="category",
            field=models.CharField(
                choices=[
                    ("legacy", "Historique"),
                    ("visibility", "Visibilité"),
                    ("analytics", "Analytics"),
                    ("promotion", "Promotion"),
                ],
                default="legacy",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="pragmadinarwallet",
            name="supplier",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="pragmadinar_wallets",
                to="user.supplier",
            ),
        ),
        migrations.AlterField(
            model_name="pragmadinarledger",
            name="entry_type",
            field=models.CharField(
                choices=[
                    ("grant", "Crédit offert"),
                    ("purchase", "Achat de Pragmadinar"),
                    ("spend", "Consommation"),
                    ("refund", "Remboursement technique"),
                    ("hold", "Réservation"),
                    ("release", "Libération de réservation"),
                    ("adjustment", "Ajustement administrateur"),
                    ("transfer", "Transfert entre portefeuilles"),
                ],
                default="spend",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="pragmadinarwallet",
            constraint=models.UniqueConstraint(
                fields=("supplier", "category"),
                name="pragmadinar_wallet_supplier_category",
            ),
        ),
        migrations.RunPython(split_existing_balances, migrations.RunPython.noop),
    ]
