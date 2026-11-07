"""Products to sell (direct order) vs products to make (quote request only)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from user import entitlements, rbac
from user.models import Category, Order, Product
from user.platform_config import seed_settings
from user.pragmadinar import seed_pragmadinar_catalog
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user


class ProductListingTypeTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        seed_pragmadinar_catalog()
        seed_settings()
        rbac.seed_roles()
        self.category = Category.objects.create(name_en="Cartons", level=1)
        self.supplier = make_supplier("Listing Co", plan="genereux")
        supplier_user = make_supplier_user(self.supplier, email="listing@example.com")
        self.supplier_client = APIClient()
        self.supplier_client.force_authenticate(supplier_user)
        self.buyer = make_buyer("listing")
        self.buyer.wilaya = "algiers"
        self.buyer.save(update_fields=["wilaya"])
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)

    def _create(self, **extra):
        return self.supplier_client.post(
            "/api/supplier/products/",
            {"name": "Boîte", "categoryId": self.category.id, **extra},
            format="multipart",
        )

    def _product(self, listing_type):
        return Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Carton",
            description="Carton",
            price=Decimal("40.00"),
            min_order_quantity=10,
            listing_type=listing_type,
        )

    def _order(self, product):
        return self.buyer_client.post(
            reverse("api_orders"),
            {"productId": product.pk, "quantity": 20, "wilaya": "algiers"},
            format="json",
        )

    def test_new_products_default_to_sell(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["listingType"], "sell")

    def test_supplier_can_create_a_product_to_make(self):
        response = self._create(listingType="make")
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(pk=response.data["id"])
        self.assertEqual(product.listing_type, Product.ListingType.MAKE)

    def test_unknown_listing_type_is_rejected(self):
        response = self._create(listingType="rent")
        self.assertEqual(response.status_code, 400)
        self.assertIn("listingType", response.data)

    def test_product_to_sell_can_be_ordered_directly(self):
        response = self._order(self._product(Product.ListingType.SELL))
        self.assertEqual(response.status_code, 201, response.data)

    def test_product_to_make_cannot_be_ordered_directly(self):
        response = self._order(self._product(Product.ListingType.MAKE))
        self.assertEqual(response.status_code, 400)
        self.assertIn("productId", response.data)
        self.assertFalse(Order.objects.exists())

    def test_public_product_payload_exposes_listing_type(self):
        product = self._product(Product.ListingType.MAKE)
        response = APIClient().get(f"/api/products/{product.pk}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["listingType"], "make")
