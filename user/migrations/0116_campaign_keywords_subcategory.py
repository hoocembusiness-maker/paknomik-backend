from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0115_supplier_is_ghost"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="keywords",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Mots-clés réservés pour une campagne de recherche.",
            ),
        ),
        migrations.AddField(
            model_name="campaign",
            name="subcategory",
            field=models.ForeignKey(
                blank=True,
                help_text="Sous-catégorie réservée pour une apparition prioritaire.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="priority_campaigns",
                to="user.category",
            ),
        ),
    ]
