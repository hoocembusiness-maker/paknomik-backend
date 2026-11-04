from django.db import migrations


def apply_v4(apps, schema_editor):
    from user.taxonomy_v4 import apply_taxonomy_v4

    apply_taxonomy_v4(dry_run=False)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    """Remap catalogue to Pacdora-inspired Famille → Type structure."""

    dependencies = [
        ("user", "0121_remove_category_is_active_is_service"),
    ]

    operations = [
        migrations.RunPython(apply_v4, noop_reverse),
    ]
