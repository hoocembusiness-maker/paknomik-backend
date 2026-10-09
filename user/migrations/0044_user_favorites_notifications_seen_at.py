"""
Django database migration `0044_user_favorites_notifications_seen_at`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0043_support_conversations"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="favorites_notifications_seen_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the supplier last opened the favorites activity page.",
                null=True,
            ),
        ),
    ]
