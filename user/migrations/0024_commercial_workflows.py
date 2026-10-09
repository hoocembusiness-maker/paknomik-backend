"""
Django database migration `0024_commercial_workflows`.
When you run `python manage.py migrate`, Django applies this file
to update tables or data. Beginners should not edit migrations by hand
unless a developer asks you to.
"""

# Generated manually for commercial workflow separation

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def populate_order_supplier(apps, schema_editor):
    Order = apps.get_model('user', 'Order')
    for order in Order.objects.select_related('product').iterator():
        if order.product_id:
            order.supplier_id = order.product.supplier_id
            order.save(update_fields=['supplier_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('user', '0023_order'),
    ]

    operations = [
        migrations.RenameField(
            model_name='order',
            old_name='user',
            new_name='buyer',
        ),
        migrations.RenameField(
            model_name='order',
            old_name='price',
            new_name='unit_price',
        ),
        migrations.RenameField(
            model_name='tendercall',
            old_name='user',
            new_name='buyer',
        ),
        migrations.AlterField(
            model_name='order',
            name='buyer',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='orders',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='order',
            name='product',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='orders',
                to='user.product',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='specifications',
            field=models.TextField(blank=True, help_text='Spécifications complémentaires saisies à la commande.'),
        ),
        migrations.AddField(
            model_name='order',
            name='supplier',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='orders',
                to='user.supplier',
            ),
        ),
        migrations.RunPython(populate_order_supplier, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='supplier',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='orders',
                to='user.supplier',
            ),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'En attente'),
                    ('confirmed', 'Confirmée'),
                    ('in_negotiation', 'En négociation'),
                    ('delivered', 'Livrée'),
                    ('cancelled', 'Annulée'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='rfqrequest',
            name='buyer',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='quote_requests',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='rfqrequest',
            name='product',
            field=models.ForeignKey(
                blank=True,
                help_text='Produit de référence optionnel.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='quote_requests',
                to='user.product',
            ),
        ),
        migrations.AddField(
            model_name='rfqrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('submitted', 'Envoyée'),
                    ('in_review', 'En étude'),
                    ('quoted', 'Devis envoyé'),
                    ('accepted', 'Acceptée'),
                    ('rejected', 'Refusée'),
                    ('cancelled', 'Annulée'),
                ],
                default='submitted',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='rfqrequest',
            name='title',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='rfqrequest',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='rfqrequest',
            name='supplier',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='quote_requests',
                to='user.supplier',
            ),
        ),
        migrations.AddField(
            model_name='tendercall',
            name='status',
            field=models.CharField(
                choices=[
                    ('open', 'Ouverte'),
                    ('closed', 'Clôturée'),
                    ('awarded', 'Attribuée'),
                    ('cancelled', 'Annulée'),
                ],
                default='open',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='tendercall',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='tendercall',
            name='buyer',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='sourcing_requests',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='tendercall',
            name='category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='sourcing_requests',
                to='user.category',
            ),
        ),
        migrations.AlterField(
            model_name='tendercall',
            name='subcategory',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='sourcing_requests_as_subcategory',
                to='user.category',
            ),
        ),
        migrations.AlterField(
            model_name='tendercall',
            name='tender_type',
            field=models.CharField(
                choices=[('simple', 'Simple'), ('detailed', 'Détaillée')],
                default='simple',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='tendercallnotification',
            name='status',
            field=models.CharField(
                choices=[
                    ('invited', 'Invité'),
                    ('viewed', 'Consultée'),
                    ('responded', 'Répondu'),
                    ('declined', 'Déclinée'),
                ],
                default='invited',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='tendercallnotification',
            name='supplier',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='sourcing_invitations',
                to='user.supplier',
            ),
        ),
        migrations.AlterField(
            model_name='tendercallnotification',
            name='tender_call',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='invitations',
                to='user.tendercall',
            ),
        ),
        migrations.CreateModel(
            name='QuoteResponse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField()),
                ('proposed_price', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('currency', models.CharField(default='DZD', max_length=8)),
                ('lead_time_days', models.PositiveIntegerField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Brouillon'),
                        ('submitted', 'Envoyée'),
                        ('accepted', 'Acceptée'),
                        ('rejected', 'Refusée'),
                    ],
                    default='submitted',
                    max_length=20,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quote_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='responses',
                    to='user.rfqrequest',
                )),
                ('supplier', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='quote_responses',
                    to='user.supplier',
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='SourcingResponse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField()),
                ('proposed_price', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('currency', models.CharField(default='DZD', max_length=8)),
                ('moq', models.PositiveIntegerField(blank=True, null=True)),
                ('lead_time_days', models.PositiveIntegerField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Brouillon'),
                        ('submitted', 'Envoyée'),
                        ('shortlisted', 'Présélectionnée'),
                        ('accepted', 'Retenue'),
                        ('rejected', 'Refusée'),
                    ],
                    default='submitted',
                    max_length=20,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('invitation', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='responses',
                    to='user.tendercallnotification',
                )),
                ('sourcing_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='responses',
                    to='user.tendercall',
                )),
                ('supplier', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sourcing_responses',
                    to='user.supplier',
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('sourcing_request', 'supplier')},
            },
        ),
    ]
