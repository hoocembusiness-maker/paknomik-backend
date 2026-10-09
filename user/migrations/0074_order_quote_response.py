from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0073_order_quote_proposal"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="quote_response",
            field=models.OneToOneField(
                blank=True,
                help_text="Réponse devis boutique (RFQ) à l'origine de cette commande.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="converted_order",
                to="user.quoteresponse",
            ),
        ),
    ]
