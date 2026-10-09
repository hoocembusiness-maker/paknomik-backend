from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0072_quote_demand_marketplace"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="quote_proposal",
            field=models.OneToOneField(
                blank=True,
                help_text="Proposition de devis à l'origine de cette commande, le cas échéant.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="converted_order",
                to="user.quoteproposal",
            ),
        ),
    ]
