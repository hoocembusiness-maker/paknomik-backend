from django.test import TestCase

from user.api.serializers import (
    _product_technical_sheet_rows,
    _read_product_attribute_values,
    _save_product_technical_attributes,
)
from user.models import Category, CategoryAttribute, Product, Supplier, TechnicalAttribute
from user.product_similarity import ProductSimilarityChecker


class ProductAttributeMultiValueTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_en="Bags", level=1)
        self.subcategory = Category.objects.create(
            name_en="Kraft bags", parent=self.category, level=2
        )
        self.supplier = Supplier.objects.create(company_name="Multi Pack", email="m@example.com")
        self.thickness = self._attribute("Thickness", "number", name_fr="Épaisseur")
        self.material = self._attribute("Material", "choice", name_fr="Matière")
        self.food_safe = self._attribute("Food safe", "boolean", name_fr="Contact alimentaire")
        self.finish = self._attribute("Finish", "text", name_fr="Finition")

    def _attribute(self, name, attribute_type, name_fr):
        attribute = TechnicalAttribute.objects.create(
            name_en=name, name_fr=name_fr, attribute_type=attribute_type
        )
        CategoryAttribute.objects.create(category=self.subcategory, attribute=attribute)
        return attribute

    def _product(self, name, items):
        product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            subcategory=self.subcategory,
            name=name,
            description=name,
        )
        _save_product_technical_attributes(product, self.category, self.subcategory, items)
        return product

    def _values(self, product, attribute):
        return [
            row["value"]
            for row in _read_product_attribute_values(product)
            if row["attributeId"] == attribute.id
        ]

    def test_number_and_choice_attributes_keep_several_values(self):
        product = self._product(
            "Sac kraft",
            [
                {"attributeId": self.thickness.id, "value": "20"},
                {"attributeId": self.thickness.id, "value": "30,5"},
                {"attributeId": self.material.id, "value": "Kraft"},
                {"attributeId": self.material.id, "value": "Recycled"},
            ],
        )
        self.assertEqual(self._values(product, self.thickness), ["20.0", "30.5"])
        self.assertEqual(self._values(product, self.material), ["Kraft", "Recycled"])

    def test_duplicate_values_are_saved_once(self):
        product = self._product(
            "Sac",
            [
                {"attributeId": self.thickness.id, "value": "20"},
                {"attributeId": self.thickness.id, "value": "20.0"},
                {"attributeId": self.material.id, "value": "Kraft"},
                {"attributeId": self.material.id, "value": "kraft"},
            ],
        )
        self.assertEqual(self._values(product, self.thickness), ["20.0"])
        self.assertEqual(self._values(product, self.material), ["Kraft"])

    def test_boolean_and_text_attributes_keep_a_single_value(self):
        product = self._product(
            "Sac",
            [
                {"attributeId": self.food_safe.id, "value": "true"},
                {"attributeId": self.food_safe.id, "value": "false"},
                {"attributeId": self.finish.id, "value": "Mat"},
                {"attributeId": self.finish.id, "value": "Brillant"},
            ],
        )
        self.assertEqual(self._values(product, self.food_safe), ["true"])
        self.assertEqual(self._values(product, self.finish), ["Mat"])

    def test_technical_sheet_lists_all_values_on_one_row(self):
        product = self._product(
            "Sac",
            [
                {"attributeId": self.thickness.id, "value": "20"},
                {"attributeId": self.thickness.id, "value": "30"},
            ],
        )
        rows = {row["label"]: row["value"] for row in _product_technical_sheet_rows(product)}
        self.assertEqual(rows["Épaisseur"], "20 / 30")

    def test_similarity_uses_the_best_matching_value(self):
        left = self._product("Sac A", [{"attributeId": self.thickness.id, "value": "20"},
                                       {"attributeId": self.thickness.id, "value": "50"}])
        right = self._product("Sac B", [{"attributeId": self.thickness.id, "value": "50"}])
        result = ProductSimilarityChecker().compare(left, right)
        detail = next(d for d in result.attribute_details if d.attribute_id == self.thickness.id)
        self.assertEqual(detail.score, 1.0)
