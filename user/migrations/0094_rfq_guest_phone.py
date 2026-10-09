from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0093_rfq_quantity_destination"),
    ]

    operations = [
        migrations.AddField(
            model_name="rfqrequest",
            name="phone",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AlterField(
            model_name="rfqrequest",
            name="email",
            field=models.EmailField(blank=True, max_length=254),
        ),
    ]
