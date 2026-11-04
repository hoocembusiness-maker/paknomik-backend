from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0116_campaign_keywords_subcategory"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="productattributevalue",
            options={"ordering": ["id"]},
        ),
        migrations.AlterUniqueTogether(
            name="productattributevalue",
            unique_together=set(),
        ),
        migrations.AddIndex(
            model_name="productattributevalue",
            index=models.Index(fields=["product", "attribute"], name="user_produc_product_4be269_idx"),
        ),
    ]
