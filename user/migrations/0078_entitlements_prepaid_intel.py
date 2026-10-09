"""Intel / promo access via Pakdinar (no plan lock on Généreux)."""

from django.db import migrations


def open_prepaid_features(apps, schema_editor):
    PlanEntitlement = apps.get_model("user", "PlanEntitlement")
    keys = [
        "feature.dashboard_current_month",
        "feature.dashboard_history",
        "feature.market_analysis",
        "feature.benchmarking",
        "feature.advanced_reports",
        "feature.sponsored_promotion",
        "feature.pak_dating",
        "feature.pdf_datasheet",
        "feature.extra_templates",
    ]
    PlanEntitlement.objects.filter(
        plan_code="genereux", key__in=keys
    ).update(limit_value=None)


def rollback_prepaid_features(apps, schema_editor):
    PlanEntitlement = apps.get_model("user", "PlanEntitlement")
    locked = {
        "feature.dashboard_current_month": 0,
        "feature.dashboard_history": 0,
        "feature.market_analysis": 0,
        "feature.benchmarking": 0,
        "feature.advanced_reports": 0,
        "feature.sponsored_promotion": 0,
        "feature.pak_dating": 0,
        "feature.pdf_datasheet": 0,
        "feature.extra_templates": 0,
    }
    for key, limit in locked.items():
        PlanEntitlement.objects.filter(plan_code="genereux", key=key).update(
            limit_value=limit
        )


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0077_resync_official_pakdinar_catalog"),
    ]

    operations = [
        migrations.RunPython(open_prepaid_features, rollback_prepaid_features),
    ]
