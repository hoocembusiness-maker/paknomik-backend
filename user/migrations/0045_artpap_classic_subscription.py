"""
Django database migration `0045_artpap_classic_subscription`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations


def set_artpap_classic(apps, schema_editor):
    Supplier = apps.get_model("user", "Supplier")
    Supplier.objects.filter(company_name__iexact="ARTPAP").update(subscription_type="Classic")


class Migration(migrations.Migration):
    dependencies = [
        ("user", "0044_user_favorites_notifications_seen_at"),
    ]

    operations = [
        migrations.RunPython(set_artpap_classic, migrations.RunPython.noop),
    ]
