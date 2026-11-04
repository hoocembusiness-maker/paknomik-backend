from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0124_quote_form_templates"),
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
                    ("sample_request", "Demande d'échantillon"),
                    ("pilotage_task_assigned", "Tâche de pilotage affectée"),
                    ("pilotage_task_blocked", "Tâche de pilotage bloquée"),
                    ("supplier_rated", "Note acheteur"),
                ],
                max_length=32,
            ),
        ),
    ]
