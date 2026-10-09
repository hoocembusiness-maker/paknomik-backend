from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0094_rfq_guest_phone"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appnotification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("quote_invited", "Fournisseur sélectionné"),
                    ("quote_proposal", "Nouvelle proposition"),
                    ("quote_lost", "Proposition perdue"),
                    ("rfq_in_review", "Demande en cours de traitement"),
                ],
                max_length=32,
            ),
        ),
    ]
