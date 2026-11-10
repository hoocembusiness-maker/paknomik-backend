"""Re-apply Pro Box dimensions so catalog cards can show L × l × H."""

from django.db import migrations


def seed_probox_dimensions(apps, schema_editor):
    from user.boite_compartiment import seed_boite_compartiment

    seed_boite_compartiment()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0139_boite_compartiment_family_attrs"),
    ]

    operations = [
        migrations.RunPython(seed_probox_dimensions, migrations.RunPython.noop),
    ]
