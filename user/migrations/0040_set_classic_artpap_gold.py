"""
Django database migration `0040_set_classic_artpap_gold`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


def set_classic_except_artpap(apps, schema_editor):
    Supplier = apps.get_model("user", "Supplier")
    Supplier.objects.all().update(subscription_type="Classic")
    Supplier.objects.filter(company_name__iexact="ARTPAP").update(subscription_type="Gold")


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0039_set_all_suppliers_gold"),
    ]

    operations = [
        migrations.RunPython(set_classic_except_artpap, migrations.RunPython.noop),
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
                default="Classic",
                max_length=100,
                null=True,
            ),
        ),
    ]
