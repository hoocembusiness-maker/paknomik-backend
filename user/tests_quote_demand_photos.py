"""Photos attached by the buyer to a quote demand."""

from __future__ import annotations

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from user import entitlements
from user.models import Category, QuoteDemand, QuoteDemandPhoto
from user.tests_crm_pragmadinar import make_buyer, make_supplier

MEDIA_ROOT = tempfile.mkdtemp()


def image_upload(name="photo.png", image_format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "#6c3b9e").save(buffer, format=image_format)
    content_type = "image/png" if image_format == "PNG" else "image/jpeg"
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class QuoteDemandPhotoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        entitlements.seed_entitlements()
        self.category = Category.objects.create(name_en="Boxes", slug="photo-boxes")
        self.subcategory = Category.objects.create(
            name_en="Gift boxes", slug="photo-gift-boxes", parent=self.category
        )
        self.supplier = make_supplier("Photo Co")
        self.client = APIClient()
        self.client.force_authenticate(make_buyer("photo"))

    def _create(self, photos):
        return self.client.post(
            reverse("api_quote_demands"),
            {
                "kind": "private",
                "categoryId": self.category.pk,
                "subcategoryId": self.subcategory.pk,
                "quantity": 500,
                "desiredLeadTimeDays": 10,
                "description": "Coffrets avec dorure",
                "selectedSupplierIds": f"[{self.supplier.pk}]",
                "photos": photos,
            },
            format="multipart",
        )

    def test_photos_are_saved_in_order_and_returned(self):
        response = self._create([image_upload("a.png"), image_upload("b.jpg", "JPEG")])
        self.assertEqual(response.status_code, 201, response.data)
        demand = QuoteDemand.objects.get(pk=response.data["id"])
        self.assertEqual(
            list(demand.photos.values_list("position", flat=True)), [0, 1]
        )

        detail = self.client.get(reverse("api_quote_demand_detail", args=[demand.pk])).data
        self.assertEqual(len(detail["photos"]), 2)
        self.assertTrue(detail["photos"][0]["url"].startswith("http"))

    def test_non_image_file_is_rejected(self):
        fake = SimpleUploadedFile("photo.png", b"not an image", content_type="image/png")
        response = self._create([fake])
        self.assertEqual(response.status_code, 400)
        self.assertIn("image valide", response.data["error"])
        self.assertFalse(QuoteDemand.objects.exists())

    def test_at_most_five_photos(self):
        response = self._create([image_upload(f"{i}.png") for i in range(6)])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(QuoteDemandPhoto.objects.exists())
