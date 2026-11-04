from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0129_paksuites_catalogue"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pragmadinarstrategyreservation",
            name="ends_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
