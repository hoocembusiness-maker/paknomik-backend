"""
Tests for minimal packaging technical attributes (Matière, Dimensions, Fermeture).
Run: py -3 manage.py test user.tests_core_packaging_attributes
"""

from django.test import TestCase

from user.core_packaging_attributes import (
    CORE_SPECS,
    category_core_coverage,
    packaging_subcategories,
    seed_core_packaging_attributes,
)
from user.models import Category, CategoryAttribute, TechnicalAttribute


class CorePackagingAttributesTests(TestCase):
    def setUp(self):
        self.parent = Category.objects.create(
            name_en="Boxes and cartons",
            name_fr="Boîtes et cartons",
            slug="boites-et-cartons",
            level=1,
        )
        self.empty_sub = Category.objects.create(
            name_en="Folding carton",
            name_fr="Boîte pliante",
            slug="boite-pliante",
            parent=self.parent,
            level=2,
        )
        self.partial_sub = Category.objects.create(
            name_en="Corrugated box",
            name_fr="Caisse américaine",
            slug="caisse-americaine",
            parent=self.parent,
            level=2,
        )
        length = TechnicalAttribute.objects.create(
            name_en="Length",
            name_fr="Longueur",
            attribute_type="text",
            attribute_level=2,
        )
        CategoryAttribute.objects.create(category=self.partial_sub, attribute=length)

    def test_seed_adds_missing_cores_only(self):
        stats = seed_core_packaging_attributes()
        self.assertEqual(stats.categories_scanned, 2)
        self.assertEqual(stats.attributes_ensured, 3)
        # empty: 3 cores; partial already has dimensions via Longueur → 2 cores
        self.assertEqual(stats.links_created, 5)
        self.assertEqual(stats.skipped_complete, 0)

        empty_cov = category_core_coverage(self.empty_sub)
        self.assertTrue(all(empty_cov.values()))

        partial_cov = category_core_coverage(self.partial_sub)
        self.assertTrue(all(partial_cov.values()))
        self.assertFalse(
            CategoryAttribute.objects.filter(
                category=self.partial_sub,
                attribute__external_id="core-packaging-dimensions",
            ).exists()
        )

    def test_seed_is_idempotent(self):
        first = seed_core_packaging_attributes()
        second = seed_core_packaging_attributes()
        self.assertEqual(first.links_created, 5)
        self.assertEqual(second.links_created, 0)
        self.assertEqual(second.skipped_complete, 2)
        self.assertEqual(
            TechnicalAttribute.objects.filter(
                external_id__in=[spec["external_id"] for spec in CORE_SPECS]
            ).count(),
            3,
        )

    def test_packaging_subcategories_ignore_services(self):
        services = Category.objects.create(
            name_en="Print services",
            name_fr="Services impression",
            slug="services-impression",
            level=1,
        )
        Category.objects.create(
            name_en="Offset",
            name_fr="Offset",
            slug="offset",
            parent=services,
            level=2,
        )
        slugs = set(packaging_subcategories().values_list("slug", flat=True))
        self.assertIn("boite-pliante", slugs)
        self.assertNotIn("offset", slugs)
