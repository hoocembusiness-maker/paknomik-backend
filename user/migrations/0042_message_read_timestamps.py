"""
Django database migration `0042_message_read_timestamps`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('user', '0041_user_profile_picture'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='read_by_buyer_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='message',
            name='read_by_supplier_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
