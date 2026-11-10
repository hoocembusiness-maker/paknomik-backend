"""Attach family technical attributes onto Boîte compartimentée / plateau."""

from django.db import migrations


def seed_family_attrs(apps, schema_editor):
    from user.boite_compartiment import seed_boite_compartiment

    seed_boite_compartiment()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0138_boite_compartimentee"),
    ]

    operations = [
        migrations.RunPython(seed_family_attrs, migrations.RunPython.noop),
    ]
