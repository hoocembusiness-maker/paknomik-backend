import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0096_pragmadinar_function_wallets"),
    ]

    operations = [
        migrations.CreateModel(
            name="PragmadinarStrategyReservation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "target_quantity",
                    models.PositiveIntegerField(),
                ),
                (
                    "period",
                    models.CharField(
                        choices=[("day", "Jour"), ("week", "Semaine"), ("month", "Mois")],
                        max_length=8,
                    ),
                ),
                (
                    "reserved_amount",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("reserved", "Réservée"),
                            ("released", "Libérée"),
                            ("consumed", "Consommée"),
                        ],
                        db_index=True,
                        default="reserved",
                        max_length=12,
                    ),
                ),
                ("ends_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "hold_ledger",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="strategy_reservation",
                        to="user.pragmadinarledger",
                    ),
                ),
                (
                    "service",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="strategy_reservations",
                        to="user.pragmadinarservice",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pragmadinar_strategy_reservations",
                        to="user.supplier",
                    ),
                ),
                (
                    "wallet",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="strategy_reservations",
                        to="user.pragmadinarwallet",
                    ),
                ),
            ],
            options={
                "verbose_name": "Pragmadinar strategy reservation",
                "verbose_name_plural": "Pragmadinar strategy reservations",
                "ordering": ["-created_at"],
            },
        ),
    ]
