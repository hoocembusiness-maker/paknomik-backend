from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0118_pragmadinar_period_strategy"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PragmadinarPeriodStrategy",
        ),
    ]
