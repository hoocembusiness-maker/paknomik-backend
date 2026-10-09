"""
Django database migration `0056_order_won_lost_status`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0055_proposal_messages_and_order_response"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "En attente"),
                    ("confirmed", "Confirmée"),
                    ("in_negotiation", "En négociation"),
                    ("won", "Gagnée"),
                    ("lost", "Perdue"),
                    ("delivered", "Livrée"),
                    ("cancelled", "Annulée"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
