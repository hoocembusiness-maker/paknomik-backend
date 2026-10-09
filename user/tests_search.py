"""
Tests for product and supplier search features.
"""

from django.test import TestCase

from user.models import Category, Product, SearchTag, Supplier
from user.product_markets import matching_market_ids_for_query
from user.product_search import filter_products_by_text, score_product_match
from user.search_engine import (
    expand_search_token,
    fold_text,
    token_matches_text,
    tokenize_query,
)
from user.supplier_search import filter_suppliers_by_text, score_supplier_match


class SearchEngineTests(TestCase):
    def test_fold_text_ignores_case_and_accents(self):
        self.assertEqual(fold_text("BoîTe"), "boite")
        self.assertEqual(fold_text("  ÉTIQUETTE  "), "etiquette")

    def test_tokenize_splits_words(self):
        self.assertEqual(tokenize_query("Boite Carton"), ["boite", "carton"])

    def test_typo_matches_bottle_synonym(self):
        self.assertTrue(token_matches_text("bouteile", "Bottle packaging for pharma"))
        variants = expand_search_token("bouteil")
        self.assertIn("bottle", variants)

    def test_market_query_is_case_insensitive(self):
        self.assertIn("pharma", matching_market_ids_for_query("PHARMA"))
        self.assertIn("cosmetique", matching_market_ids_for_query("cosmétique"))


class ProductSearchTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            company_name="PlastPack",
            email="plast@example.com",
            presentation="Specialiste plastique et etiquettes",
        )
        self.category = Category.objects.create(
            name_en="Bottles & Jars",
            name_fr="Bouteilles et bocaux",
            level=1,
        )
        self.product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Flacon PET",
            description="Bottle for cosmetic and pharma sectors.",
            target_markets=["pharma", "cosmetique"],
            is_active=True,
        )
        SearchTag.objects.create(tag="Bottle", tag_fr="Bouteille")

    def test_description_and_market_search(self):
        qs = filter_products_by_text(Product.objects.all(), "pharma")
        self.assertIn(self.product, list(qs))

    def test_typo_finds_bottle_via_description(self):
        qs = filter_products_by_text(Product.objects.all(), "bouteile")
        self.assertIn(self.product, list(qs))

    def test_all_tokens_required(self):
        qs = filter_products_by_text(Product.objects.all(), "bottle inconnu")
        self.assertNotIn(self.product, list(qs))

    def test_description_scores_higher_than_name_only(self):
        tokens = tokenize_query("pharma")
        score = score_product_match(self.product, tokens)
        self.assertGreater(score, 0)


class SupplierSearchTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            company_name="FoamTech",
            email="foam@example.com",
            presentation="Fabricant de mousse et foam industriel",
        )
        self.category = Category.objects.create(name_en="Foam", name_fr="Mousse", level=1)
        self.supplier.interested_categories.add(self.category)

    def test_fuzzy_plastic_supplier(self):
        plastic_supplier = Supplier.objects.create(
            company_name="Alger Plastique",
            email="plastic@example.com",
            presentation="Injection plastique PET et PE",
        )
        other = Supplier.objects.create(
            company_name="PaperOnly",
            email="paper@example.com",
            presentation="Carton only",
        )
        qs = filter_suppliers_by_text(Supplier.objects.all(), "plastc")
        self.assertIn(plastic_supplier, list(qs))
        self.assertNotIn(other, list(qs))

    def test_mousse_matches_foam_supplier(self):
        qs = filter_suppliers_by_text(Supplier.objects.all(), "mousse")
        self.assertIn(self.supplier, list(qs))

    def test_supplier_score_requires_all_tokens(self):
        score = score_supplier_match(self.supplier, tokenize_query("mousse"))
        self.assertGreater(score, 0)
        self.assertEqual(score_supplier_match(self.supplier, tokenize_query("mousse absent")), 0)
