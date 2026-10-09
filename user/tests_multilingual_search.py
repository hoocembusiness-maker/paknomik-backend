"""
Tests for search in French, Arabic, and mixed queries.
"""

from django.test import TestCase

from user.models import Category, Product, SearchTag, Supplier
from user.packaging_taxonomy import expand_token_variants
from user.product_search import filter_products_by_text, score_product_match
from user.search_engine import (
    expand_search_token,
    fold_text,
    normalize_phrase,
    phrase_in_text,
    token_matches_text,
    tokenize_query,
)
from user.search_normalize import (
    arabic_ta_marbuta_variant,
    normalize_arabic,
    normalize_french,
    strip_arabic_definite_article,
)


class FrenchNormalizationTests(TestCase):
    def test_lowercase_and_accents(self):
        self.assertEqual(normalize_french('Étiquettes'), 'etiquettes')
        self.assertEqual(fold_text('BoîTe'), 'boite')

    def test_apostrophes_and_hyphens(self):
        self.assertEqual(normalize_french("Boîte à pizza"), 'boite a pizza')
        self.assertEqual(normalize_french('Film étirable'), 'film etirable')

    def test_plural_variants(self):
        variants = expand_search_token('sac')
        self.assertIn('sacs', variants)
        variants = expand_search_token('sacs')
        self.assertIn('sac', variants)


class ArabicNormalizationTests(TestCase):
    def test_alef_and_diacritics(self):
        self.assertEqual(normalize_arabic('أكياس'), 'اكياس')
        self.assertEqual(normalize_arabic('عبوات'), 'عبوات')

    def test_ta_marbuta_and_definite_article(self):
        self.assertEqual(normalize_arabic('زجاجات'), 'زجاجات')
        self.assertEqual(normalize_arabic('زجاجه'), 'زجاجه')
        self.assertEqual(strip_arabic_definite_article('الملصقات'), 'ملصقات')
        alt = arabic_ta_marbuta_variant('زجاجه')
        self.assertIn('ة', alt)

    def test_definite_article_does_not_block_matching(self):
        with_article = expand_search_token('الملصقات', for_db=True)
        without_article = expand_search_token('ملصقات', for_db=True)
        self.assertTrue(with_article & without_article)
        self.assertIn('تغليف', tokenize_query('التغليف المرن'))
        self.assertIn('مرن', tokenize_query('التغليف المرن'))


class PackagingTaxonomyTests(TestCase):
    def test_kraft_bag_cross_language(self):
        sac_variants = expand_token_variants('sac kraft')
        bag_variants = expand_token_variants('kraft bag')
        arabic_variants = expand_token_variants('أكياس كرافت')
        self.assertTrue(sac_variants & bag_variants)
        self.assertTrue(sac_variants & arabic_variants)

    def test_dosage_form_not_merged_with_blister_without_context(self):
        tablet_variants = expand_token_variants('comprime')
        self.assertNotIn('blister', tablet_variants)

    def test_dosage_form_expands_to_pharma_packaging_with_hint(self):
        variants = expand_token_variants('comprime', query_tokens=['emballage', 'comprime'])
        self.assertIn('blister', variants)
        variants = expand_token_variants('قرص', query_tokens=['تغليف', 'قرص'])
        self.assertIn('blister', variants)

    def test_transliteration_doypack(self):
        variants = expand_token_variants('دوياك')
        self.assertIn('doypack', variants)


class MultilingualProductSearchTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            company_name='PackMultilang',
            email='multi@example.com',
            presentation='Emballage industriel multilingue',
        )
        self.cat_flexible = Category.objects.create(
            name_en='Flexible Packaging',
            name_fr='Emballage flexible',
            name_ar='تغليف مرن',
            level=1,
        )
        self.cat_carton = Category.objects.create(
            name_en='Corrugated Boxes',
            name_fr='Carton ondulé',
            name_ar='كرتون مموج',
            level=1,
        )
        self.cat_labels = Category.objects.create(
            name_en='Labels',
            name_fr='Étiquettes',
            name_ar='ملصقات',
            level=1,
        )
        self.cat_pharma = Category.objects.create(
            name_en='Pharmaceutical Packaging',
            name_fr='Emballage pharmaceutique',
            name_ar='تغليف دوائي',
            level=1,
        )
        self.cat_bottles = Category.objects.create(
            name_en='Bottles',
            name_fr='Bouteilles',
            name_ar='زجاجات',
            level=1,
        )

        self.kraft_bag = Product.objects.create(
            supplier=self.supplier,
            category=self.cat_flexible,
            name='Sac kraft alimentaire',
            description='Kraft paper bag for food and shopping.',
            is_active=True,
        )
        self.tag_kraft = SearchTag.objects.create(
            tag='kraft bag', tag_fr='sac kraft', tag_ar='أكياس كرافت',
        )

        self.pizza_box = Product.objects.create(
            supplier=self.supplier,
            category=self.cat_carton,
            name='Boîte pizza',
            name_ar='علبة بيتزا',
            description='Carton ondulé pour pizza et restauration.',
            is_active=True,
        )
        self.tag_pizza = SearchTag.objects.create(
            tag='pizza box', tag_fr='boîte pizza', tag_ar='علبة بيتزا',
        )

        self.label_product = Product.objects.create(
            supplier=self.supplier,
            category=self.cat_labels,
            name='Etiquette autocollante',
            description='Adhesive label for products and barcodes.',
            is_active=True,
        )
        self.tag_label = SearchTag.objects.create(
            tag='adhesive label',
            tag_fr='étiquette autocollante',
            tag_ar='ملصق لاصق',
        )

        self.stretch_film = Product.objects.create(
            supplier=self.supplier,
            category=self.cat_flexible,
            name='Film étirable',
            description='Stretch film for pallet wrapping.',
            is_active=True,
        )
        self.tag_stretch = SearchTag.objects.create(
            tag='stretch film',
            tag_fr='film étirable',
            tag_ar='فيلم قابل للتمدد',
        )

        self.pharma_pack = Product.objects.create(
            supplier=self.supplier,
            category=self.cat_pharma,
            name='Blister pharma',
            description='Pharmaceutical packaging blister alu-alu for tablets.',
            target_markets=['pharma'],
            is_active=True,
        )
        self.tag_pharma = SearchTag.objects.create(
            tag='pharmaceutical packaging',
            tag_fr='emballage pharmaceutique',
            tag_ar='تغليف دوائي',
        )

        self.plastic_bottle = Product.objects.create(
            supplier=self.supplier,
            category=self.cat_bottles,
            name='Bouteille plastique PET',
            name_ar='زجاجة بلاستيكية',
            description='Plastic bottle PET for beverages.',
            is_active=True,
        )
        self.tag_bottle = SearchTag.objects.create(
            tag='plastic bottle',
            tag_fr='bouteille plastique',
            tag_ar='زجاجة بلاستيكية',
        )

        self.kraft_bag.search_tags.set([self.tag_kraft])
        self.pizza_box.search_tags.set([self.tag_pizza])
        self.label_product.search_tags.set([self.tag_label])
        self.stretch_film.search_tags.set([self.tag_stretch])
        self.pharma_pack.search_tags.set([self.tag_pharma])
        self.plastic_bottle.search_tags.set([self.tag_bottle])

    def _ids(self, query):
        return {p.pk for p in filter_products_by_text(Product.objects.all(), query)}

    def test_pizza_arabic_token_matching(self):
        tokens = tokenize_query('علبة بيتزا')
        self.assertTrue(
            token_matches_text('علبة', self.pizza_box.name_ar, query_tokens=tokens),
        )
        self.assertTrue(
            token_matches_text('بيتزا', self.pizza_box.name_ar, query_tokens=tokens),
        )
        self.assertGreater(
            score_product_match(self.pizza_box, tokens, query='علبة بيتزا'),
            0,
        )

    def test_french_queries(self):
        self.assertIn(self.kraft_bag.pk, self._ids('sac kraft'))
        self.assertIn(self.pizza_box.pk, self._ids('bo\u00eete \u00e0 pizza'))
        self.assertIn(self.pizza_box.pk, self._ids('boite pizza'))
        self.assertIn(self.label_product.pk, self._ids('étiquette autocollante'))
        self.assertIn(self.stretch_film.pk, self._ids('film étirable'))
        self.assertIn(self.pharma_pack.pk, self._ids('emballage pharmaceutique'))

    def test_arabic_queries(self):
        self.assertIn(self.kraft_bag.pk, self._ids('أكياس كرافت'))
        self.assertIn(self.pizza_box.pk, self._ids('علبة بيتزا'))
        self.assertIn(self.label_product.pk, self._ids('ملصق لاصق'))
        self.assertIn(self.stretch_film.pk, self._ids('فيلم قابل للتمدد'))
        self.assertIn(self.pharma_pack.pk, self._ids('تغليف دوائي'))
        self.assertIn(self.plastic_bottle.pk, self._ids('زجاجة بلاستيكية'))

    def test_english_queries(self):
        self.assertIn(self.kraft_bag.pk, self._ids('kraft bag'))
        self.assertIn(self.pizza_box.pk, self._ids('pizza box'))
        self.assertIn(self.label_product.pk, self._ids('adhesive label'))
        self.assertIn(self.stretch_film.pk, self._ids('stretch film'))
        self.assertIn(self.pharma_pack.pk, self._ids('pharmaceutical packaging'))

    def test_cross_language_pharma_query(self):
        query = 'emballage \u062f\u0648\u0627\u0626\u064a'
        tokens = tokenize_query(query)
        self.assertGreater(
            score_product_match(self.pharma_pack, tokens, query=query),
            0,
        )
        self.assertIn(self.pharma_pack.pk, self._ids(query))

    def test_mixed_queries(self):
        self.assertIn(self.kraft_bag.pk, self._ids('sac كرافت'))
        self.assertIn(self.pizza_box.pk, self._ids('علبة pizza'))
        self.assertIn(self.pharma_pack.pk, self._ids('emballage دوائي'))
        self.assertIn(self.plastic_bottle.pk, self._ids('bouteille بلاستيك'))

    def test_arabic_normalization_variants(self):
        self.assertIn(self.kraft_bag.pk, self._ids('اكياس'))
        self.assertIn(self.label_product.pk, self._ids('ملصقات'))
        self.assertIn(self.plastic_bottle.pk, self._ids('زجاجات'))

    def test_phrase_ranks_above_single_token(self):
        phrase_score = score_product_match(
            self.pizza_box,
            tokenize_query('boîte pizza'),
            query='boîte pizza',
        )
        single_score = score_product_match(
            self.pizza_box,
            tokenize_query('pizza'),
            query='pizza',
        )
        self.assertGreater(phrase_score, single_score)

    def test_phrase_in_text_helper(self):
        self.assertTrue(
            phrase_in_text('boîte pizza', 'Carton ondulé pour pizza — Boîte pizza premium')
        )

    def test_tablet_packaging_query_matches_pharma_not_dosage_alone(self):
        self.assertNotIn(self.pharma_pack.pk, self._ids('comprimé'))
        self.assertIn(self.pharma_pack.pk, self._ids('emballage pour comprimés'))
        self.assertIn(self.pharma_pack.pk, self._ids('تغليف الأقراص'))
        self.assertIn(self.pharma_pack.pk, self._ids('tablet packaging'))
