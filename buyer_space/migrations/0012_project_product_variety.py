from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('buyer_space', '0011_library_product_recommended_suite'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectProductVariety',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('label_text', models.CharField(blank=True, default='', max_length=200)),
                ('cap_color', models.CharField(blank=True, default='', max_length=80)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='varieties', to='buyer_space.projectproductlink')),
            ],
            options={
                'verbose_name': 'Variété de produit',
                'verbose_name_plural': 'Variétés de produit',
                'ordering': ['sort_order', 'id'],
            },
        ),
    ]
