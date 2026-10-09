"""Réserve le CRM commercial à Paknomik Business (décision produit).

Ne touche aucune donnée CRM existante : seule la ligne d'entitlement change,
donc un fournisseur qui repasse en Business retrouve ses enregistrements.
"""

from django.db import migrations


def crm_business_only(apps, schema_editor):
    PlanEntitlement = apps.get_model("user", "PlanEntitlement")
    PlanEntitlement.objects.update_or_create(
        plan_code="genereux",
        key="feature.crm",
        defaults={"label": "CRM commercial", "limit_value": 0, "is_enabled": True},
    )


def crm_open_to_all(apps, schema_editor):
    PlanEntitlement = apps.get_model("user", "PlanEntitlement")
    PlanEntitlement.objects.filter(
        plan_code="genereux", key="feature.crm"
    ).update(limit_value=None)


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0061_seed_rbac_entitlements_settings"),
    ]

    operations = [
        migrations.RunPython(crm_business_only, crm_open_to_all),
    ]
