"""Boîte compartimentée type, layout attribute, and Pro Box dimensions."""

from decimal import Decimal

from django.test import TestCase

from user.api.serializers import _product_dimensions
from user.boite_compartiment import (
    COMPARTMENT_TYPE_SLUG,
    REPARTITION_EXTERNAL_ID,
    seed_boite_compartiment,
)
from user.models import (
    AttributeOption,
    Category,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    Supplier,
)
from user.taxonomy_v4 import apply_taxonomy_v4


class BoiteCompartimentTests(TestCase):
    def setUp(self):
        apply_taxonomy_v4()
        self.supplier = Supplier.objects.create(
            company_name="Pro Box",
            email="smi.kecar24@gmail.com",
        )
        family = Category.objects.get(slug="boites-et-cartons")
        plateau = Category.objects.get(slug="boite-plateau")
        self.products = {
            "cplus": Product.objects.create(
                supplier=self.supplier,
                category=family,
                subcategory=plateau,
                name="Boîte compartimentée Modèle C+ — 25 × 10 × 4 cm",
                description="x",
                price=Decimal("130"),
            ),
            "eplus": Product.objects.create(
                supplier=self.supplier,
                category=family,
                subcategory=plateau,
                name="Boîte compartimentée Modèle E+ — 16 × 12 × 4 cm",
                description="x",
                price=Decimal("120"),
            ),
            "s": Product.objects.create(
                supplier=self.supplier,
                category=family,
                subcategory=plateau,
                name="Boîte compartimentée Modèle S",
                description="x",
                price=Decimal("100"),
            ),
            "dd": Product.objects.create(
                supplier=self.supplier,
                category=family,
                subcategory=plateau,
                name="Boîte compartimentée allongée Modèle DD",
                description="x",
                price=Decimal("80"),
            ),
            "plain": Product.objects.create(
                supplier=self.supplier,
                category=family,
                subcategory=plateau,
                name="Boîte plateau Modèle F — 8 × 8 × 4 cm",
                description="x",
                price=Decimal("50"),
            ),
        }

    def test_seed_adds_type_layout_and_probox_dimensions(self):
        stats = seed_boite_compartiment()
        self.assertGreaterEqual(stats["updated"], 5)
        self.assertTrue(Category.objects.filter(slug=COMPARTMENT_TYPE_SLUG).exists())

        layout = CategoryAttribute.objects.get(
            category__slug=COMPARTMENT_TYPE_SLUG,
            attribute__external_id=REPARTITION_EXTERNAL_ID,
        )
        labels = set(
            AttributeOption.objects.filter(category_attribute=layout).values_list(
                "value_fr", flat=True
            )
        )
        for expected in ("2x2", "2x3", "4 alignées", "3 alignées", "4x4", "1 + 2 demi"):
            self.assertIn(expected, labels)

        cplus = Product.objects.get(pk=self.products["cplus"].pk)
        self.assertEqual(cplus.subcategory.slug, COMPARTMENT_TYPE_SLUG)
        self.assertEqual(_product_dimensions(cplus), "25 × 10 × 4 cm")
        self.assertEqual(
            ProductAttributeValue.objects.get(
                product=cplus,
                attribute__external_id="core-packaging-dimensions",
            ).txt_value,
            "25 × 10 × 4 cm",
        )
        self.assertEqual((cplus.attributes or {}).get("dimensions"), "25 × 10 × 4 cm")
        self.assertTrue(
            ProductAttributeValue.objects.filter(
                product=cplus,
                attribute__external_id=REPARTITION_EXTERNAL_ID,
                txt_value="2x5",
            ).exists()
        )

        s_box = Product.objects.get(pk=self.products["s"].pk)
        self.assertIn("12 × 8 × 4", s_box.name)
        self.assertEqual(_product_dimensions(s_box), "12 × 8 × 4 cm")
        self.assertTrue(
            ProductAttributeValue.objects.filter(
                product=s_box,
                attribute__external_id=REPARTITION_EXTERNAL_ID,
                txt_value="2x3",
            ).exists()
        )

        dd = Product.objects.get(pk=self.products["dd"].pk)
        self.assertIn("20 × 4 × 4", dd.name)
        self.assertEqual(_product_dimensions(dd), "20 × 4 × 4 cm")
        self.assertTrue(
            ProductAttributeValue.objects.filter(
                product=dd,
                attribute__external_id=REPARTITION_EXTERNAL_ID,
                txt_value="4 alignées",
            ).exists()
        )

        plain = Product.objects.get(pk=self.products["plain"].pk)
        self.assertEqual(plain.subcategory.slug, "boite-plateau")
        self.assertEqual(_product_dimensions(plain), "8 × 8 × 4 cm")
        self.assertEqual(
            ProductAttributeValue.objects.get(
                product=plain,
                attribute__external_id="core-packaging-dimensions",
            ).txt_value,
            "8 × 8 × 4 cm",
        )
        self.assertEqual((plain.attributes or {}).get("dimensions"), "8 × 8 × 4 cm")

    def test_seed_is_idempotent(self):
        first = seed_boite_compartiment()
        second = seed_boite_compartiment()
        self.assertEqual(first["updated"], second["updated"])
        self.assertEqual(
            ProductAttributeValue.objects.filter(
                product=self.products["cplus"],
                attribute__external_id=REPARTITION_EXTERNAL_ID,
            ).count(),
            1,
        )
