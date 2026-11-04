from django.db import migrations


class Migration(migrations.Migration):
    """No-op placeholder.

    An earlier draft briefly added Category.is_active / is_service; those columns
    were removed because they broke historical Category RunPython migrations.
    This migration keeps the graph stable for databases that already applied it.
    """

    dependencies = [
        ("user", "0120_taxonomy_v3_family_product_type"),
    ]

    operations = []
