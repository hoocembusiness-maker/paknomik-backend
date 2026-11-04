from django.db import migrations


def add_bouchon_externe(apps, schema_editor):
    SuiteEnsemble = apps.get_model("buyer_space", "SuiteEnsemble")
    SuiteTaxon = apps.get_model("buyer_space", "SuiteTaxon")
    SuiteComponentDefinition = apps.get_model("buyer_space", "SuiteComponentDefinition")
    SuiteComponentLine = apps.get_model("buyer_space", "SuiteComponentLine")

    ensemble = SuiteEnsemble.objects.filter(code="PKS-COS-003").first()
    if ensemble is None:
        return

    taxon = SuiteTaxon.objects.filter(code="capuchon-h").first()
    if taxon is None:
        taxon = SuiteTaxon.objects.filter(label_fr__iexact="Capuchon").first()
    if taxon is None:
        taxon = SuiteTaxon.objects.create(
            kind="type",
            code="bouchon-externe",
            label_fr="Bouchon externe",
            origin="Composant",
        )

    definition, _ = SuiteComponentDefinition.objects.get_or_create(
        code="bouchon-externe",
        defaults={
            "label": "Bouchon externe",
            "visual_kind": "bouchon",
            "taxon_id": taxon.id,
        },
    )
    if definition.label != "Bouchon externe" or definition.visual_kind != "bouchon" or definition.taxon_id != taxon.id:
        definition.label = "Bouchon externe"
        definition.visual_kind = "bouchon"
        definition.taxon_id = taxon.id
        definition.save(update_fields=["label", "visual_kind", "taxon"])

    existing = SuiteComponentLine.objects.filter(
        ensemble=ensemble,
        definition=definition,
        role="Protection",
    ).first()
    if existing is None:
        # Place after the spray pump: flacon(0), spray(1), bouchon externe(2), …
        for line in SuiteComponentLine.objects.filter(ensemble=ensemble, sort_order__gte=2):
            line.sort_order += 1
            line.save(update_fields=["sort_order"])
        SuiteComponentLine.objects.create(
            ensemble=ensemble,
            definition=definition,
            taxon_id=taxon.id,
            role="Protection",
            level="primary",
            status="required",
            material="PP",
            sort_order=2,
        )


def remove_bouchon_externe(apps, schema_editor):
    SuiteEnsemble = apps.get_model("buyer_space", "SuiteEnsemble")
    SuiteComponentDefinition = apps.get_model("buyer_space", "SuiteComponentDefinition")
    SuiteComponentLine = apps.get_model("buyer_space", "SuiteComponentLine")

    ensemble = SuiteEnsemble.objects.filter(code="PKS-COS-003").first()
    definition = SuiteComponentDefinition.objects.filter(code="bouchon-externe").first()
    if ensemble is None or definition is None:
        return
    SuiteComponentLine.objects.filter(
        ensemble=ensemble,
        definition=definition,
        role="Protection",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("buyer_space", "0013_product_kit_assignments"),
    ]

    operations = [
        migrations.RunPython(add_bouchon_externe, remove_bouchon_externe),
    ]
