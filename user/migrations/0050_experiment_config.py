"""
Django database migration `0050_experiment_config`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

from django.db import migrations, models
import django.db.models.deletion


def seed_home_redesign_experiment(apps, schema_editor):
    ExperimentConfig = apps.get_model("user", "ExperimentConfig")
    ExperimentConfig.objects.get_or_create(
        key="home_redesign_2026",
        defaults={
            "enabled": False,
            "split_percent_v2": 0,
            "forced_variant": None,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0049_remove_gobelets_barquettes_subcategories"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExperimentConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("key", models.CharField(db_index=True, max_length=80, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("split_percent_v2", models.PositiveSmallIntegerField(default=0)),
                (
                    "forced_variant",
                    models.CharField(
                        blank=True,
                        choices=[("control", "Control"), ("v2", "V2")],
                        max_length=20,
                        null=True,
                    ),
                ),
                ("impressions_control", models.PositiveIntegerField(default=0)),
                ("impressions_v2", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="experiment_configs_updated",
                        to="user.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "Experiment config",
                "verbose_name_plural": "Experiment configs",
            },
        ),
        migrations.RunPython(seed_home_redesign_experiment, migrations.RunPython.noop),
    ]
