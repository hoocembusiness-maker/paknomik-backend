"""Supplier second brand color used as button backgrounds."""

from django.test import TestCase
from rest_framework.test import APIClient

from user.tests_crm_pragmadinar import make_supplier, make_supplier_user


class BrandButtonColorTests(TestCase):
    def setUp(self):
        self.supplier = make_supplier("Boutons Co", plan="genereux")
        user = make_supplier_user(self.supplier, email="boutons@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_profile_round_trip(self):
        response = self.client.patch(
            "/api/supplier/profile/",
            {"brandButtonColor": "#e85d04"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["brandButtonColor"], "#e85d04")
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.brand_button_color, "#e85d04")

        public = APIClient().get(f"/api/suppliers/{self.supplier.id}/")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.data["brandButtonColor"], "#e85d04")
