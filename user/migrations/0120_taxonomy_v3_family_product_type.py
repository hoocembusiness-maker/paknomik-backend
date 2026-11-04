from django.db import migrations


def apply_v3(apps, schema_editor):
    from user.taxonomy_v3 import apply_taxonomy_v3

    apply_taxonomy_v3(dry_run=False)


def noop_reverse(apps, schema_editor):
    # Non-destructive forward; reverse would require a full snapshot restore.
    pass


class Migration(migrations.Migration):
    """Remap Category tree to Famille → Type de produit (taxonomy v3).

    No schema change: navigation uses slug allow-lists in ``user.taxonomy_v3``.
    """

    dependencies = [
        ("user", "0119_remove_pragmadinar_period_strategy"),
    ]

    operations = [
        migrations.RunPython(apply_v3, noop_reverse),
    ]
