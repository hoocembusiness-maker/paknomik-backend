"""Tests for hierarchical SearchTerm map."""

from django.test import TestCase

from user.bobines_seed import apply_bobines_catalog
from user.models import Category, SearchTerm
from user.search_keyword_resolver import resolve_search_map, resolve_subcategories_for_crm
from user.search_map_seed import seed_bobines_search_map
from user.search_term_normalize import normalize_search_term


class SearchKeywordMapTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        apply_bobines_catalog(dry_run=False)
        seed_bobines_search_map()

    def test_bobine_papier_maps_to_subcategory(self):
        hits = resolve_search_map("bobine papier")
        self.assertTrue(hits)
        self.assertEqual(hits[0].subcategory_id is not None, True)
        sub = Category.objects.get(pk=hits[0].subcategory_id)
        self.assertEqual(sub.slug, "bobine_papier")

    def test_paper_roll_multilingual(self):
        hits = resolve_search_map("paper roll")
        slugs = {
            Category.objects.get(pk=h.subcategory_id).slug
            for h in hits
            if h.subcategory_id
        }
        self.assertIn("bobine_papier", slugs)

    def test_film_is_ambiguous_multi_mapping(self):
        term = SearchTerm.objects.get(normalized_term=normalize_search_term("film"))
        self.assertTrue(term.is_ambiguous)
        hits = resolve_search_map("film")
        sub_ids = {h.subcategory_id for h in hits if h.subcategory_id}
        self.assertGreaterEqual(len(sub_ids), 2)
        self.assertEqual(resolve_subcategories_for_crm("film"), [])

    def test_film_pvc_prefers_pvc_subcategory(self):
        hits = resolve_search_map("film pvc thermoretractable")
        self.assertTrue(hits)
        top = hits[0]
        sub = Category.objects.get(pk=top.subcategory_id)
        self.assertEqual(sub.slug, "bobine_pvc")

    def test_crm_subcategory_for_specific_query(self):
        subs = resolve_subcategories_for_crm("bobine aluminium")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].slug, "bobine_aluminium")
