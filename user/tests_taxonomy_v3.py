"""Legacy test module — taxonomy v3 shim now runs Pacdora-inspired v4."""

from django.test import TestCase
from rest_framework.test import APIClient

from user.models import Category, Product, Supplier
from user.taxonomy_v3 import (
    ACTIVE_FAMILY_SLUGS,
    LEGACY_TYPE_SLUG,
    apply_taxonomy_v3,
)


class TaxonomyV3CompatTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            company_name="Tax Co", email="tax-co@example.com"
        )

    def test_apply_taxonomy_v3_delegates_to_v4(self):
        report = apply_taxonomy_v3()
        self.assertEqual(report["version"], 4)
        self.assertIn("boites-et-cartons", ACTIVE_FAMILY_SLUGS)
        self.assertEqual(Category.objects.filter(slug="sac-en-papier").count(), 1)

    def test_api_lists_active_families(self):
        apply_taxonomy_v3()
        families = APIClient().get("/api/categories/").json()
        slugs = {row["slug"] for row in families}
        self.assertIn("services-impression", slugs)
        self.assertNotIn("a-reviser", slugs)

    def test_ambiguous_product_goes_to_legacy_review(self):
        orphan_family = Category.objects.create(
            name_en="Odd", name_fr="Odd", slug="odd-family-unique", level=1
        )
        orphan = Product.objects.create(
            id=99002,
            supplier=self.supplier,
            category=orphan_family,
            subcategory=None,
            name="Produit ambigu sans type",
            description="x",
        )
        report = apply_taxonomy_v3()
        orphan.refresh_from_db()
        self.assertIn(orphan.id, report["ambiguous_product_ids"])
        self.assertEqual(orphan.subcategory.slug, LEGACY_TYPE_SLUG)
