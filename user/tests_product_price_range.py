"""Product price: fixed price or price range (min – max)."""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from user.api.serializers import _product_price_badge
from user.models import Category, Product, Supplier, User


class ProductPriceRangeTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_en="Boxes", slug="range-boxes")
        self.supplier = Supplier.objects.create(company_name="Range SA", email="range@example.com")
        user = User.objects.create_user(
            username="range_supplier", email="range_supplier@example.com", password="TestPass123!"
        )
        user.supplier = self.supplier
        user.save(update_fields=["supplier"])
        self.client = APIClient()
        self.client.force_authenticate(user)

    def _create(self, **prices):
        return self.client.post(
            "/api/supplier/products/",
            {"name": "Boîte kraft", "categoryId": self.category.id, **prices},
            format="multipart",
        )

    def test_range_is_saved_and_labelled(self):
        response = self._create(price="1200", priceMax="1500.50")
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(pk=response.data["id"])
        self.assertEqual(product.price, Decimal("1200"))
        self.assertEqual(product.price_max, Decimal("1500.50"))
        self.assertIsNone(product.fixed_unit_price)
        self.assertEqual(_product_price_badge(product), "1 200 – 1 500,50 DA / unité")

    def test_fixed_price_keeps_working(self):
        response = self._create(price="900", priceMax="")
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(pk=response.data["id"])
        self.assertIsNone(product.price_max)
        self.assertEqual(product.fixed_unit_price, Decimal("900"))
        self.assertEqual(_product_price_badge(product), "900 DA / unité")

    def test_max_must_be_above_min(self):
        response = self._create(price="1500", priceMax="1200")
        self.assertEqual(response.status_code, 400)
        self.assertIn("priceMax", response.data)

    def test_range_needs_a_minimum(self):
        response = self._create(priceMax="1200")
        self.assertEqual(response.status_code, 400)
        self.assertIn("price", response.data)

    def test_switching_back_to_fixed_price_clears_the_max(self):
        product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Sac",
            description="Sac",
            price=Decimal("10"),
            price_max=Decimal("20"),
        )
        response = self.client.patch(
            f"/api/supplier/products/{product.id}/",
            {"price": "15", "priceMax": ""},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        product.refresh_from_db()
        self.assertEqual(product.price, Decimal("15"))
        self.assertIsNone(product.price_max)

    def test_clearing_the_price_clears_the_range(self):
        product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Sac",
            description="Sac",
            price=Decimal("10"),
            price_max=Decimal("20"),
        )
        self.client.patch(
            f"/api/supplier/products/{product.id}/", {"price": ""}, format="multipart"
        )
        product.refresh_from_db()
        self.assertIsNone(product.price)
        self.assertIsNone(product.price_max)
        self.assertEqual(_product_price_badge(product), "Négociable")
