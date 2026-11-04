from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('user', '0131_alter_pragmadinarstrategyreservation_period'),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='external_id',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='technicalattribute',
            name='external_id',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
