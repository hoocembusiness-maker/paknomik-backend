from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0097_pragmadinar_strategy_reservation"),
    ]

    operations = [
        migrations.AddField(
            model_name="pragmadinarstrategyreservation",
            name="start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pragmadinarstrategyreservation",
            name="end_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pragmadinarstrategyreservation",
            name="rhythm",
            field=models.CharField(
                choices=[
                    ("daily_fixed", "Quotidien fix"),
                    ("weekly_fixed", "Hebdomadaire fix"),
                    ("variable", "Variable"),
                    ("weekdays", "Jours spécifiques de semaine"),
                    ("daily_limit", "Limite quotidienne"),
                    ("weekly_limit", "Limite hebdomadaire"),
                ],
                default="variable",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="pragmadinarstrategyreservation",
            name="rhythm_detail",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
