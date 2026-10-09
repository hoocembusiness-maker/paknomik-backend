from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0067_seed_bobines_catalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="interested_subcategories",
            field=models.ManyToManyField(
                blank=True,
                help_text="Sous-catégories d'intérêt déclarées par l'acheteur (signaux CRM).",
                related_name="buyers_interested",
                to="user.category",
            ),
        ),
        migrations.AlterField(
            model_name="opportunity",
            name="source",
            field=models.CharField(
                choices=[
                    ("organic_search", "Recherche organique"),
                    ("category_intent", "Intérêt catégorie"),
                    ("store_visit", "Visite boutique"),
                    ("product_view", "Consultation de produit"),
                    ("favorite", "Favori"),
                    ("quote_request", "Demande de devis"),
                    ("order", "Commande"),
                    ("direct_contact", "Contact direct"),
                    ("pakmedia", "Pakmedia"),
                    ("pak_dating", "Pak-dating accepté"),
                    ("referral", "Recommandation"),
                ],
                default="direct_contact",
                max_length=32,
            ),
        ),
    ]
