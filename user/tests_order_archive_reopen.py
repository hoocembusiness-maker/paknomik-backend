"""Supplier actions on a cancelled order: relaunch, contact and archive.

Run with: python manage.py test user.tests_order_archive_reopen
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from user import entitlements
from user.models import AuditLog, Category, Message, Order, OrderStatusEvent, Product
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user


class OrderArchiveReopenTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        category = Category.objects.create(name_en="Cartons", level=1)
        self.supplier = make_supplier("Arch Co", plan="genereux")
        self.supplier_client = APIClient()
        self.supplier_client.force_authenticate(
            make_supplier_user(self.supplier, email="arch@example.com")
        )
        self.product = Product.objects.create(
            supplier=self.supplier,
            name="Carton",
            description="Carton",
            category=category,
            subcategory=category,
            price=Decimal("40.00"),
            min_order_quantity=10,
        )
        self.buyer = make_buyer("arch")
        self.order = Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=self.product,
            quantity=20,
            unit_price=Decimal("40.00"),
            status=Order.Status.CANCELLED,
            origin=Order.Origin.PRODUCT_PAGE,
        )

    def _archive(self, archived=True, order=None):
        url = reverse("api_supplier_order_archive", args=[(order or self.order).pk])
        return self.supplier_client.post(url, {"archived": archived}, format="json")

    def _list(self):
        return {o["id"]: o for o in self.supplier_client.get(reverse("api_supplier_orders")).json()}

    def test_cancelled_order_exposes_the_three_actions(self):
        data = self._list()[self.order.pk]
        self.assertTrue(data["canReopen"])
        self.assertTrue(data["canArchive"])
        self.assertFalse(data["archived"])
        self.assertEqual(data["buyerId"], self.buyer.pk)

    def test_archive_and_unarchive(self):
        response = self._archive()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["archived"])
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.supplier_archived_at)
        self.assertTrue(AuditLog.objects.filter(action="order.archived").exists())

        response = self._archive(archived=False)
        self.assertFalse(response.json()["archived"])
        self.order.refresh_from_db()
        self.assertIsNone(self.order.supplier_archived_at)

    def test_open_order_cannot_be_archived(self):
        self.order.status = Order.Status.PENDING
        self.order.save(update_fields=["status"])
        self.assertEqual(self._archive().status_code, 409)

    def test_reopen_returns_order_to_pending_and_unarchives(self):
        self._archive()
        url = reverse("api_supplier_order_reopen", args=[self.order.pk])
        response = self.supplier_client.post(url, {"note": "Stock revenu"}, format="json")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], Order.Status.PENDING)
        self.assertFalse(body["archived"])
        self.assertTrue(
            OrderStatusEvent.objects.filter(
                order=self.order,
                from_status=Order.Status.CANCELLED,
                to_status=Order.Status.PENDING,
            ).exists()
        )
        self.assertTrue(
            Message.objects.filter(buyer=self.buyer, content__contains="relancée").exists()
        )
        self.assertTrue(AuditLog.objects.filter(action="order.reopened").exists())

    def test_reopen_refused_unless_cancelled(self):
        self.order.status = Order.Status.CONFIRMED
        self.order.save(update_fields=["status"])
        url = reverse("api_supplier_order_reopen", args=[self.order.pk])
        self.assertEqual(self.supplier_client.post(url, {}, format="json").status_code, 409)

    def test_other_supplier_cannot_touch_the_order(self):
        other = make_supplier("Other Co", plan="genereux")
        client = APIClient()
        client.force_authenticate(make_supplier_user(other, email="other@example.com"))
        archive_url = reverse("api_supplier_order_archive", args=[self.order.pk])
        reopen_url = reverse("api_supplier_order_reopen", args=[self.order.pk])
        self.assertEqual(client.post(archive_url, {}, format="json").status_code, 404)
        self.assertEqual(client.post(reopen_url, {}, format="json").status_code, 404)

    def test_buyer_payload_has_no_archive_flag(self):
        client = APIClient()
        client.force_authenticate(self.buyer)
        self._archive()
        orders = client.get(reverse("api_buyer_orders")).json()
        payload = orders if isinstance(orders, list) else orders.get("results", [])
        self.assertTrue(payload)
        self.assertNotIn("archived", payload[0])
