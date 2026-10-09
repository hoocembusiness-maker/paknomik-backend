import shutil
import tempfile
from io import BytesIO, StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from user.models import (
    Category,
    Opportunity,
    PragmadinarLedger,
    Product,
    RFQRequest,
    RFQRequestPhoto,
    SampleRequest,
    SupplierSettings,
)
from user.photo_uploads import has_metadata
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user

MEDIA_ROOT = tempfile.mkdtemp(prefix="paknomik-test-media-")


def jpeg_with_exif(name="photo.jpg"):
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif[0x0110] = "PhoneModel"
    buffer = BytesIO()
    Image.new("RGB", (24, 16), "purple").save(buffer, format="JPEG", exif=exif.tobytes())
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class SampleRequestTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.supplier = make_supplier("Sample Co")
        self.supplier_user = make_supplier_user(self.supplier)
        category = Category.objects.create(name_en="Boxes", name_fr="Boîtes", slug="sample-boxes")
        self.product = Product.objects.create(
            supplier=self.supplier, name="Coffret", category=category
        )
        self.client = APIClient()

    def _settings(self, **page):
        SupplierSettings.objects.update_or_create(
            supplier=self.supplier, defaults={"page_design": page}
        )

    def _send(self, **extra):
        data = {
            "supplierId": self.supplier.pk,
            "name": "Amel",
            "phone": "+213555123456",
            "deliveryAddress": "12 rue Didouche, Alger",
            **extra,
        }
        return self.client.post(reverse("api_sample_requests"), data, format="multipart")

    def test_quantity_is_a_snapshot_of_the_supplier_setting(self):
        self._settings(sampleQuantity="8")
        response = self._send(productId=self.product.pk)
        self.assertEqual(response.status_code, 201, response.data)
        sample = SampleRequest.objects.get()
        self.assertEqual(sample.quantity, 8)
        self.assertEqual(sample.product, self.product)

        self._settings(sampleQuantity="20")
        sample.refresh_from_db()
        self.assertEqual(sample.quantity, 8)

    def test_default_quantity_is_five_and_clamped(self):
        self.assertEqual(self._send().status_code, 201)
        self._settings(sampleQuantity="500")
        self.assertEqual(self._send().status_code, 201)
        self.assertEqual(
            list(SampleRequest.objects.order_by("id").values_list("quantity", flat=True)),
            [5, 50],
        )

    def test_disabled_samples_are_refused(self):
        self._settings(samplesEnabled="false")
        self.assertEqual(self._send().status_code, 403)
        self.assertFalse(SampleRequest.objects.exists())

    def test_delivery_address_is_required(self):
        self.assertEqual(self._send(deliveryAddress="").status_code, 400)

    def test_free_and_synced_to_crm_as_sample_request(self):
        self._send()
        self.assertFalse(PragmadinarLedger.objects.filter(wallet__supplier=self.supplier).exists())
        opportunity = Opportunity.objects.get(supplier=self.supplier)
        self.assertEqual(opportunity.source, Opportunity.Source.SAMPLE_REQUEST)
        self.assertEqual(opportunity.stage, Opportunity.Stage.REQUEST_RECEIVED)

    def test_photos_are_stored_without_exif(self):
        response = self._send(photos=[jpeg_with_exif()])
        self.assertEqual(response.status_code, 201, response.data)
        photo = SampleRequest.objects.get().photos.get()
        with photo.image.open("rb") as handle:
            self.assertFalse(has_metadata(handle))

    def test_invalid_photos_are_rejected_server_side(self):
        fake = SimpleUploadedFile("photo.jpg", b"not an image", content_type="image/jpeg")
        self.assertEqual(self._send(photos=[fake]).status_code, 400)
        too_many = [jpeg_with_exif(f"p{i}.jpg") for i in range(6)]
        self.assertEqual(self._send(photos=too_many).status_code, 400)
        self.assertFalse(SampleRequest.objects.exists())

    def test_supplier_lists_and_moves_status(self):
        self._send()
        sample = SampleRequest.objects.get()
        self.client.force_authenticate(self.supplier_user)
        listing = self.client.get(reverse("api_supplier_sample_requests"))
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data[0]["quantity"], 5)
        url = reverse("api_supplier_sample_request_status", args=[sample.pk])
        self.assertEqual(self.client.post(url, {"status": "accepted"}).status_code, 409)
        self.assertEqual(self.client.post(url, {"status": "in_review"}).status_code, 200)
        self.assertEqual(self.client.post(url, {"status": "accepted"}).status_code, 200)

    def test_public_page_design_exposes_sample_settings(self):
        self._settings(samplesEnabled="false", sampleQuantity="12")
        data = self.client.get(f"/api/suppliers/{self.supplier.pk}/").data
        self.assertEqual(data["pageDesign"]["samplesEnabled"], "false")
        self.assertEqual(data["pageDesign"]["sampleQuantity"], "12")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class BoutiqueQuotePhotoTests(TestCase):
    def setUp(self):
        self.supplier = make_supplier("Photo Co")
        self.client = APIClient()

    def test_boutique_quote_accepts_photos_without_exif(self):
        response = self.client.post(
            reverse("api_rfq"),
            {
                "supplierId": self.supplier.pk,
                "name": "Karim",
                "phone": "+213555000111",
                "quantity": "500",
                "photos": [jpeg_with_exif()],
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        photo = RFQRequest.objects.get().photos.get()
        with photo.image.open("rb") as handle:
            self.assertFalse(has_metadata(handle))

    def test_backfill_command_strips_existing_exif(self):
        rfq = RFQRequest.objects.create(supplier=self.supplier, name="Old", phone="1")
        photo = RFQRequestPhoto.objects.create(rfq=rfq, image=jpeg_with_exif())
        with photo.image.open("rb") as handle:
            self.assertTrue(has_metadata(handle))
        out = StringIO()
        call_command("strip_photo_exif", stdout=out)
        self.assertIn("1 photo(s) nettoyées", out.getvalue())
        photo.refresh_from_db()
        with photo.image.open("rb") as handle:
            self.assertFalse(has_metadata(handle))
