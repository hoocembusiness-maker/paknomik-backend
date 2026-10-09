"""
Django database migration `0039_set_all_suppliers_gold`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


def set_all_suppliers_gold(apps, schema_editor):
    Supplier = apps.get_model("user", "Supplier")
    Supplier.objects.all().update(subscription_type="Gold")


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0038_proposal_templates"),
    ]

    operations = [
        migrations.RunPython(set_all_suppliers_gold, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="supplier",
            name="subscription_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Classic", "Classic"),
                    ("Extra", "Extra"),
                    ("Gold", "Gold"),
                ],
                default="Gold",
                max_length=100,
                null=True,
            ),
        ),
    ]
