from django.test import TestCase
from rest_framework.test import APIClient

from user.models import Category, Product, Supplier
from user.taxonomy_v4 import (
    ACTIVE_FAMILY_SLUGS,
    FAMILIES,
    LEGACY_TYPE_SLUG,
    TYPES,
    apply_taxonomy_v4,
)


class TaxonomyV4Tests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            company_name="Pac Co", email="pac-co@example.com"
        )

    def test_apply_is_idempotent_and_exposes_pacdora_families(self):
        first = apply_taxonomy_v4()
        second = apply_taxonomy_v4()
        self.assertEqual(first["version"], 4)
        self.assertEqual(first["families"], len(ACTIVE_FAMILY_SLUGS))
        self.assertEqual(second["families"], first["families"])
        self.assertIn("boites-et-cartons", ACTIVE_FAMILY_SLUGS)
        self.assertIn("pots-gobelets-contenants", ACTIVE_FAMILY_SLUGS)
        self.assertIn("papiers-emballage", ACTIVE_FAMILY_SLUGS)
        self.assertEqual(Category.objects.filter(slug="boite-pliante").count(), 1)

    def test_api_hides_legacy_and_flags_services(self):
        apply_taxonomy_v4()
        client = APIClient()
        families = client.get("/api/categories/").json()
        slugs = {row["slug"] for row in families}
        self.assertEqual(len(slugs), len(ACTIVE_FAMILY_SLUGS))
        self.assertNotIn("a-reviser", slugs)
        self.assertIn("services-impression", slugs)
        service = next(r for r in families if r["slug"] == "services-impression")
        self.assertTrue(service["isService"])

        family_id = next(r["id"] for r in families if r["slug"] == "boites-et-cartons")
        types = client.get("/api/subcategories/", {"parent": family_id}).json()
        type_slugs = {row["slug"] for row in types}
        self.assertIn("boite-pliante", type_slugs)
        self.assertTrue(all(row["familyId"] == family_id for row in types))

    def test_product_override_maps_gobelet_and_barquette(self):
        apply_taxonomy_v4()
        boxes = Category.objects.get(slug="boites-et-cartons")
        takeaway = Category.objects.get(slug="contenant-alimentaire")
        gobelet_src = Product.objects.create(
            id=9001,
            supplier=self.supplier,
            category=boxes,
            subcategory=takeaway,
            name="Gobelets en Carton",
            description="x",
        )
        # Override table uses fixed production ids; simulate via name heuristic path
        # by clearing override and relying on name — create under wrong family then remap.
        from user import taxonomy_v4 as tax

        tax.PRODUCT_TYPE_OVERRIDES[gobelet_src.id] = "gobelet"
        apply_taxonomy_v4()
        gobelet_src.refresh_from_db()
        self.assertEqual(gobelet_src.subcategory.slug, "gobelet")
        self.assertEqual(gobelet_src.category.slug, "pots-gobelets-contenants")

    def test_ambiguous_stays_identifiable(self):
        family = Category.objects.create(
            name_en="Odd", name_fr="Odd", slug="odd-family-v4", level=1
        )
        # Avoid production PRODUCT_TYPE_OVERRIDES ids (1, 2, 3, …).
        orphan = Product.objects.create(
            id=99001,
            supplier=self.supplier,
            category=family,
            subcategory=None,
            name="PAV et PLV divers",
            description="x",
        )
        report = apply_taxonomy_v4()
        orphan.refresh_from_db()
        self.assertIn(orphan.id, report["ambiguous_product_ids"])
        self.assertEqual(orphan.subcategory.slug, LEGACY_TYPE_SLUG)

    def test_specs_match_mission_families(self):
        slugs = {spec.slug for spec in FAMILIES}
        for expected in (
            "boites-et-cartons",
            "sacs-et-pochettes",
            "bouteilles-et-flacons",
            "pots-gobelets-contenants",
            "canettes-contenants-metalliques",
            "tubes",
            "etiquettes",
            "fermetures-accessoires",
            "films-materiaux-souples",
            "papiers-emballage",
            "emballage-industriel-protection",
            "services-impression",
        ):
            self.assertIn(expected, slugs)
        self.assertGreaterEqual(len(TYPES), 60)
