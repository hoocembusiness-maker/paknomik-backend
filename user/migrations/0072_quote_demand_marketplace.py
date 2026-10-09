from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill_market_fields(apps, schema_editor):
    QuoteDemand = apps.get_model("user", "QuoteDemand")
    QuoteDemandSelection = apps.get_model("user", "QuoteDemandSelection")
    PakdinarService = apps.get_model("user", "PakdinarService")

    selected_demand_ids = set(
        QuoteDemandSelection.objects.values_list("demand_id", flat=True).distinct()
    )
    cutoff = timezone.now() - timedelta(days=7)

    for demand in QuoteDemand.objects.all().iterator():
        if demand.kind != "public":
            demand.visible_on_market = False
            demand.market_published_at = None
            demand.admin_queue = False
            demand.market_removed_reason = ""
        elif demand.id in selected_demand_ids:
            demand.visible_on_market = False
            demand.market_published_at = None
            demand.admin_queue = False
            demand.market_removed_reason = "selected_supplier"
        elif demand.status != "open":
            demand.visible_on_market = False
            demand.market_published_at = demand.created_at
            demand.admin_queue = False
            demand.market_removed_reason = ""
        else:
            anchor = demand.created_at
            demand.market_published_at = anchor
            if anchor < cutoff:
                demand.visible_on_market = False
                demand.admin_queue = True
                demand.market_removed_reason = "age_limit"
            else:
                demand.visible_on_market = True
                demand.admin_queue = False
                demand.market_removed_reason = ""
        demand.save(
            update_fields=[
                "visible_on_market",
                "market_published_at",
                "admin_queue",
                "market_removed_reason",
            ]
        )

    PakdinarService.objects.filter(code="commercial.category_leads").update(
        business_access="unlimited"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0071_visibility_metering_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="quotedemand",
            name="admin_queue",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="quotedemand",
            name="market_published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="quotedemand",
            name="market_removed_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("selected_supplier", "Fournisseur(s) sélectionné(s)"),
                    ("age_limit", "Durée marché dépassée (7 j)"),
                    ("buyer_relaunched", "Relancée sur le marché"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="quotedemand",
            name="visible_on_market",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddIndex(
            model_name="quotedemand",
            index=models.Index(
                fields=["admin_queue", "status", "-created_at"],
                name="user_quote__admin_q_6a0b0d_idx",
            ),
        ),
        migrations.RunPython(backfill_market_fields, migrations.RunPython.noop),
    ]
