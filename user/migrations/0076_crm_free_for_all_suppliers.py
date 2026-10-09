"""CRM de base gratuit pour tous les fournisseurs (modèle 100 % Pakdinar).

Ne supprime aucune donnée CRM : seule la ligne d'entitlement Généreux change.
"""

from django.db import migrations


def crm_free_for_all(apps, schema_editor):
    PlanEntitlement = apps.get_model("user", "PlanEntitlement")
    PlanEntitlement.objects.filter(
        plan_code="genereux", key="feature.crm"
    ).update(limit_value=None)


def crm_business_only_rollback(apps, schema_editor):
    PlanEntitlement = apps.get_model("user", "PlanEntitlement")
    PlanEntitlement.objects.filter(
        plan_code="genereux", key="feature.crm"
    ).update(limit_value=0)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0075_backfill_order_outcome_crm"),
    ]

    operations = [
        migrations.RunPython(crm_free_for_all, crm_business_only_rollback),
    ]
