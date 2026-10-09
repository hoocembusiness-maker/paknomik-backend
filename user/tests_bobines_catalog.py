"""
Tests for Bobines catalog seed and product technical attributes.
Run: python manage.py test user.tests_bobines_catalog
"""

from django.test import TestCase

from user.api.serializers import _save_product_technical_attributes
from user.bobines_seed import apply_bobines_catalog
from user.catalog_bobines import ATTRIBUTES_BY_SUBCATEGORY_SLUG, PARENT_SLUG, SUBCATEGORIES
from user.entitlements import PRODUCTS_MAX, QuotaExceeded, assert_can_add_product
from user.models import (
    Category,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    Supplier,
    TechnicalAttribute,
    User,
)
from user.packaging_taxonomy import concept_for_token


class BobinesCatalogSeedTests(TestCase):
    def test_seed_creates_parent_and_six_subcategories(self):
        apply_bobines_catalog()
        parent = Category.objects.get(slug=PARENT_SLUG, parent__isnull=True)
        self.assertEqual(parent.name_fr, "Bobines")
        subs = Category.objects.filter(parent=parent).order_by("sort_order")
        self.assertEqual(subs.count(), len(SUBCATEGORIES))
        slugs = set(subs.values_list("slug", flat=True))
        expected = {row["slug"] for row in SUBCATEGORIES}
        self.assertEqual(slugs, expected)

    def test_seed_is_idempotent(self):
        apply_bobines_catalog()
        counts_first = {
            "categories": Category.objects.filter(slug=PARENT_SLUG).count()
            + Category.objects.filter(parent__slug=PARENT_SLUG).count(),
            "attributes": TechnicalAttribute.objects.filter(
                external_id__startswith="v5-bobine"
            ).count(),
            "links": CategoryAttribute.objects.filter(
                category__parent__slug=PARENT_SLUG
            ).count(),
        }
        apply_bobines_catalog()
        counts_second = {
            "categories": Category.objects.filter(slug=PARENT_SLUG).count()
            + Category.objects.filter(parent__slug=PARENT_SLUG).count(),
            "attributes": TechnicalAttribute.objects.filter(
                external_id__startswith="v5-bobine"
            ).count(),
            "links": CategoryAttribute.objects.filter(
                category__parent__slug=PARENT_SLUG
            ).count(),
        }
        self.assertEqual(counts_first, counts_second)

    def test_each_subcategory_has_distinct_attribute_links(self):
        apply_bobines_catalog()
        parent = Category.objects.get(slug=PARENT_SLUG)
        for row in SUBCATEGORIES:
            sub = Category.objects.get(slug=row["slug"], parent=parent)
            expected = len(ATTRIBUTES_BY_SUBCATEGORY_SLUG[row["slug"]])
            actual = CategoryAttribute.objects.filter(category=sub).count()
            self.assertEqual(
                actual,
                expected,
                msg=f"{row['slug']} expected {expected} attributes, got {actual}",
            )


class BobinesProductAttributeTests(TestCase):
    def setUp(self):
        apply_bobines_catalog()
        self.parent = Category.objects.get(slug=PARENT_SLUG)
        self.supplier = Supplier.objects.create(
            company_name="Bobines SA",
            email="bobines@example.com",
        )
        self.user = User.objects.create_user(
            username="bobine_supplier",
            email="bobine_supplier@example.com",
            password="TestPass123!",
        )
        self.user.supplier = self.supplier
        self.user.save(update_fields=["supplier"])

    def _sub(self, slug: str) -> Category:
        return Category.objects.get(slug=slug, parent=self.parent)

    def _attr_id(self, sub: Category, external_id: str) -> int:
        link = CategoryAttribute.objects.select_related("attribute").get(
            category=sub,
            attribute__external_id=external_id,
        )
        return link.attribute_id

    def _create_bobine(self, slug: str, name: str, items: list[dict]) -> Product:
        sub = self._sub(slug)
        product = Product.objects.create(
            supplier=self.supplier,
            category=self.parent,
            subcategory=sub,
            name=name,
            description=name,
        )
        _save_product_technical_attributes(
            product, self.parent, sub, items
        )
        return product

    def test_paper_reel_stores_paper_type_and_grammage(self):
        sub = self._sub("bobine_papier")
        product = self._create_bobine(
            "bobine_papier",
            "Kraft 80g",
            [
                {
                    "attributeId": self._attr_id(sub, "v5-bobine-papier-type"),
                    "value": "Kraft",
                },
                {
                    "attributeId": self._attr_id(sub, "v5-bobine-papier-grammage"),
                    "value": "80",
                },
                {
                    "attributeId": self._attr_id(sub, "v5-bobine-attr-width"),
                    "value": "1200",
                },
            ],
        )
        values = {
            av.attribute.external_id: av
            for av in product.attribute_values.select_related("attribute")
        }
        self.assertEqual(values["v5-bobine-papier-type"].txt_value, "Kraft")
        self.assertAlmostEqual(values["v5-bobine-papier-grammage"].num_value, 80.0)
        self.assertAlmostEqual(values["v5-bobine-attr-width"].num_value, 1200.0)

    def test_aluminium_reel_stores_type(self):
        sub = self._sub("bobine_aluminium")
        product = self._create_bobine(
            "bobine_aluminium",
            "Alu food",
            [
                {
                    "attributeId": self._attr_id(sub, "v5-bobine-alu-type"),
                    "value": "Aluminium alimentaire",
                },
            ],
        )
        av = product.attribute_values.select_related("attribute").get()
        self.assertEqual(av.txt_value, "Aluminium alimentaire")

    def test_composite_custom_structure_text(self):
        sub = self._sub("bobine_laminee_composite")
        product = self._create_bobine(
            "bobine_laminee_composite",
            "Custom laminate",
            [
                {
                    "attributeId": self._attr_id(sub, "v5-bobine-composite-structure"),
                    "value": "Autre structure",
                },
                {
                    "attributeId": self._attr_id(
                        sub, "v5-bobine-composite-structure-desc"
                    ),
                    "value": "Papier / PE / EVOH / PE",
                },
            ],
        )
        by_id = {
            av.attribute.external_id: av.txt_value
            for av in product.attribute_values.select_related("attribute")
        }
        self.assertEqual(by_id["v5-bobine-composite-structure"], "Autre structure")
        self.assertIn("EVOH", by_id["v5-bobine-composite-structure-desc"])

    def test_injected_attribute_from_other_subcategory_is_ignored(self):
        sub_paper = self._sub("bobine_papier")
        sub_pvc = self._sub("bobine_pvc")
        product = Product.objects.create(
            supplier=self.supplier,
            category=self.parent,
            subcategory=sub_paper,
            name="Inject test",
            description="test",
        )
        pvc_type_id = self._attr_id(sub_pvc, "v5-bobine-pvc-type")
        _save_product_technical_attributes(
            product,
            self.parent,
            sub_paper,
            [{"attributeId": pvc_type_id, "value": "PVC souple"}],
        )
        self.assertEqual(product.attribute_values.count(), 0)

    def test_all_six_subcategories_accept_minimal_product(self):
        for row in SUBCATEGORIES:
            sub = self._sub(row["slug"])
            type_specs = [
                spec
                for spec in ATTRIBUTES_BY_SUBCATEGORY_SLUG[row["slug"]]
                if spec.get("is_required")
            ]
            items = []
            for spec in type_specs:
                attr_id = self._attr_id(sub, spec["external_id"])
                if spec["attribute_type"] == "choice":
                    value = spec["options"][0][0]
                else:
                    value = "test"
                items.append({"attributeId": attr_id, "value": value})
            product = self._create_bobine(row["slug"], f"Prod {row['slug']}", items)
            self.assertTrue(product.pk)
            self.assertGreaterEqual(product.attribute_values.count(), len(type_specs))


class BobinesSearchAndPlansTests(TestCase):
    def setUp(self):
        apply_bobines_catalog()
        self.parent = Category.objects.get(slug=PARENT_SLUG)
        self.supplier = Supplier.objects.create(
            company_name="Plan Test",
            email="plan@example.com",
        )

    def test_bobine_search_concept_token(self):
        self.assertEqual(concept_for_token("bobine"), "bobines")
        self.assertEqual(concept_for_token("bobines"), "bobines")

    def test_genereux_product_limit_applies_to_bobines(self):
        sub = Category.objects.get(slug="bobine_speciale", parent=self.parent)
        for i in range(3):
            Product.objects.create(
                supplier=self.supplier,
                category=self.parent,
                subcategory=sub,
                name=f"Bobine {i}",
                description="x",
                is_active=True,
            )
        with self.assertRaises(QuotaExceeded) as ctx:
            assert_can_add_product(self.supplier)
        self.assertEqual(ctx.exception.key, PRODUCTS_MAX)
