from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0114_pragmadinar_ledger_balances"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="is_ghost",
            field=models.BooleanField(
                default=False,
                help_text="Masque le fournisseur et ses produits sur le site public. Le compte reste dans l'administration.",
            ),
        ),
    ]
