from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0092_pragmadinar_model_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="rfqrequest",
            name="quantity_estimate",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="rfqrequest",
            name="destination",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="rfqrequest",
            name="message",
            field=models.TextField(blank=True),
        ),
    ]
