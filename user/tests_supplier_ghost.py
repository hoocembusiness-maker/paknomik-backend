from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from user.models import Category, Product, Supplier

User = get_user_model()


class GhostSupplierTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name_en="Boxes", slug="boxes", level=1)
        self.supplier = Supplier.objects.create(
            company_name="Embalium",
            email="embalium@example.com",
        )
        self.product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Caisse démo",
            description="Produit de démonstration",
        )
        self.staff = User.objects.create_user(
            username="admin-ghost",
            email="admin-ghost@example.com",
            password="secret-pass",
            is_staff=True,
        )

    def test_public_catalog_hides_a_ghost_supplier(self):
        self.assertEqual(len(self.client.get("/api/suppliers/").json()), 1)
        self.assertEqual(self.client.get(f"/api/suppliers/{self.supplier.pk}/").status_code, 200)
        self.assertEqual(len(self.client.get("/api/products/").json()), 1)

        self.supplier.is_ghost = True
        self.supplier.save(update_fields=["is_ghost"])

        self.assertEqual(self.client.get("/api/suppliers/").json(), [])
        self.assertEqual(self.client.get(f"/api/suppliers/{self.supplier.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/api/products/").json(), [])
        self.assertEqual(self.client.get(f"/api/products/{self.product.pk}/").status_code, 404)
        order = self.client.post(
            "/api/orders/",
            {
                "productId": self.product.pk,
                "quantity": 1,
                "guestName": "Visiteur",
                "guestPhone": "0555000000",
            },
            format="json",
        )
        self.assertEqual(order.status_code, 400)

    def test_admin_can_mark_embalium_as_ghost(self):
        self.client.force_authenticate(self.staff)
        response = self.client.patch(
            f"/api/admin/suppliers/{self.supplier.pk}/",
            {"profile": {"isGhost": True}},
            format="json",
            HTTP_X_SUPPLIER_SETTINGS_CODE="4655bomb",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["profile"]["isGhost"])
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_ghost)
        self.assertEqual(self.client.get("/api/suppliers/").json(), [])
