"""Buyer can rate a supplier only after the order is marked delivered."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from user.models import Category, Order, OrderSupplierReview, Product
from user.tests_crm_pragmadinar import make_buyer, make_supplier


class OrderSupplierReviewTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name_en="Cartons", level=1)
        self.supplier = make_supplier("Note Co")
        self.product = Product.objects.create(
            supplier=self.supplier,
            name="Carton",
            description="Carton",
            category=category,
            subcategory=category,
            price=Decimal("40.00"),
            min_order_quantity=10,
        )
        self.buyer = make_buyer("note")
        self.other = make_buyer("other-note")
        self.client = APIClient()
        self.client.force_authenticate(self.buyer)
        self.order = Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=self.product,
            quantity=20,
            unit_price=Decimal("40.00"),
            status=Order.Status.SHIPPED,
            origin=Order.Origin.PRODUCT_PAGE,
        )

    def _rate(self, rating=4, comment="Livraison soignée", order=None, client=None):
        api = client or self.client
        return api.patch(
            reverse("api_buyer_order_detail", args=[(order or self.order).pk]),
            {"action": "rate", "rating": rating, "comment": comment},
            format="json",
        )

    def test_shipped_order_cannot_be_rated(self):
        response = self._rate()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "order_not_delivered")
        self.assertFalse(OrderSupplierReview.objects.exists())

    def test_delivered_order_can_be_rated_and_updated(self):
        self.order.status = Order.Status.DELIVERED
        self.order.save(update_fields=["status"])

        response = self._rate()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["canRate"])
        self.assertEqual(response.json()["reviewRating"], 4)
        self.assertEqual(response.json()["reviewComment"], "Livraison soignée")
        self.assertEqual(OrderSupplierReview.objects.get(order=self.order).rating, 4)

        response = self._rate(rating=5, comment="Encore mieux")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OrderSupplierReview.objects.filter(order=self.order).count(), 1)
        self.assertEqual(response.json()["reviewRating"], 5)

        listed = self.client.get(reverse("api_buyer_orders")).json()
        row = next(item for item in listed if item["id"] == self.order.pk)
        self.assertEqual(row["reviewRating"], 5)
        self.assertTrue(row["canRate"])

    def test_completed_order_can_still_be_rated(self):
        self.order.status = Order.Status.COMPLETED
        self.order.save(update_fields=["status"])
        self.assertEqual(self._rate(rating=3, comment="").status_code, 200)

    def test_rating_must_be_between_1_and_5(self):
        self.order.status = Order.Status.DELIVERED
        self.order.save(update_fields=["status"])
        response = self._rate(rating=6)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_rating")

    def test_another_buyer_cannot_rate_the_order(self):
        self.order.status = Order.Status.DELIVERED
        self.order.save(update_fields=["status"])
        other = APIClient()
        other.force_authenticate(self.other)
        self.assertEqual(self._rate(client=other).status_code, 404)
