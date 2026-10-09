from django.db import migrations, models
import django.db.models.deletion


def seed_map(apps, schema_editor):
    from user.search_map_seed import seed_bobines_search_map

    seed_bobines_search_map()


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0068_user_interested_subcategories_category_intent"),
    ]

    operations = [
        migrations.CreateModel(
            name="SearchTerm",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("term", models.CharField(max_length=200)),
                ("normalized_term", models.CharField(db_index=True, max_length=200)),
                (
                    "language",
                    models.CharField(
                        choices=[
                            ("fr", "Français"),
                            ("ar", "Arabe"),
                            ("en", "Anglais"),
                            ("dz", "Local / dialecte"),
                        ],
                        default="fr",
                        max_length=8,
                    ),
                ),
                (
                    "term_type",
                    models.CharField(
                        choices=[
                            ("canonical", "Canonique"),
                            ("synonym", "Synonyme"),
                            ("technical", "Technique"),
                            ("commercial", "Commercial"),
                            ("abbreviation", "Abréviation"),
                            ("translation", "Traduction"),
                            ("common_misspelling", "Faute courante"),
                            ("local_term", "Terme local"),
                        ],
                        default="canonical",
                        max_length=32,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "is_ambiguous",
                    models.BooleanField(
                        default=False,
                        help_text="Terme générique (ex. film, rouleau) — ne force pas une seule sous-catégorie.",
                    ),
                ),
                ("priority", models.PositiveSmallIntegerField(default=50)),
                ("weight", models.PositiveSmallIntegerField(default=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-priority", "term"],
            },
        ),
        migrations.CreateModel(
            name="SearchQueryMiss",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("original_query", models.CharField(max_length=255)),
                (
                    "normalized_query",
                    models.CharField(db_index=True, max_length=255),
                ),
                ("language_hint", models.CharField(blank=True, max_length=8)),
                ("results_count", models.PositiveIntegerField(default=0)),
                (
                    "suggested_subcategory_id",
                    models.IntegerField(blank=True, null=True),
                ),
                ("buyer_id", models.IntegerField(blank=True, null=True)),
                ("source", models.CharField(default="products", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "verbose_name": "Recherche sans résultat",
                "verbose_name_plural": "Recherches sans résultat",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SearchTermMapping",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("attribute_value", models.CharField(blank=True, max_length=200)),
                ("confidence", models.PositiveSmallIntegerField(default=80)),
                ("priority", models.PositiveSmallIntegerField(default=50)),
                ("context", models.CharField(blank=True, max_length=120)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "attribute",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="search_term_mappings",
                        to="user.technicalattribute",
                    ),
                ),
                (
                    "category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_term_mappings",
                        to="user.category",
                    ),
                ),
                (
                    "search_term",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mappings",
                        to="user.searchterm",
                    ),
                ),
                (
                    "subcategory",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search_term_mappings_as_sub",
                        to="user.category",
                    ),
                ),
            ],
            options={
                "ordering": ["-priority", "-confidence"],
            },
        ),
        migrations.AddIndex(
            model_name="searchterm",
            index=models.Index(
                fields=["normalized_term", "is_active"],
                name="user_search_normali_8f3c0d_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="searchterm",
            constraint=models.UniqueConstraint(
                fields=("normalized_term", "language", "term_type"),
                name="uniq_search_term_norm_lang_type",
            ),
        ),
        migrations.AddIndex(
            model_name="searchtermmapping",
            index=models.Index(
                fields=["is_active", "subcategory"],
                name="user_search_is_acti_5a1b2c_idx",
            ),
        ),
        migrations.RunPython(seed_map, migrations.RunPython.noop),
    ]
