"""
Django database migration `0047_user_orders_notifications_seen_at`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0046_order_guest_contact"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="orders_notifications_seen_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the user last opened the orders page (navbar badge).",
                null=True,
            ),
        ),
    ]
