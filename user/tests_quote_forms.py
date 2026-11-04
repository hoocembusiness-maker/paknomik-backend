"""Typed quote forms: category schemas, supplier overrides, frozen buyer specs."""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from user.models import (
    Category,
    QuoteFormField,
    QuoteFormTemplate,
    RFQRequest,
    RFQRequestEvent,
    SupplierQuoteForm,
)
from user.quote_forms import seed_quote_form_templates
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user


class TypedQuoteFormTests(TestCase):
    def setUp(self):
        self.family = Category.objects.get(slug="boites-et-cartons")
        self.box = Category.objects.get(slug="boite-pliante")
        seed_quote_form_templates()
        self.supplier = make_supplier("Cartonnier")
        self.supplier.interested_subcategories.add(self.box)
        self.owner = make_supplier_user(self.supplier)
        self.other = make_supplier("Autre")
        self.other_user = make_supplier_user(self.other, email="other@example.com")
        self.buyer = make_buyer("devis")
        self.buyer.is_verified_client = True
        self.buyer.save(update_fields=["is_verified_client"])
        self.client_api = APIClient()

    def _specs(self, **overrides):
        specs = {
            "longueur": 200,
            "largeur": 100,
            "hauteur": 50,
            "type_carton": "Carton compact",
            "fermeture": "Rabat rentrant",
            "impression": "Aucune",
        }
        specs.update(overrides)
        return specs

    def test_type_inherits_family_form(self):
        self.client_api.force_authenticate(self.buyer)
        response = self.client_api.get(
            reverse("api_quote_form"),
            {"supplierId": self.supplier.id, "categoryId": self.box.id},
        )
        self.assertEqual(response.status_code, 200)
        keys = [field["key"] for field in response.data["fields"]]
        self.assertIn("cannelure", keys)
        self.assertEqual(response.data["quantityUnit"], "pièces")
        self.assertEqual(response.data["category"]["name"], "Boîte pliante")

    def test_print_followup_is_hidden_until_print_is_chosen(self):
        field = QuoteFormField.objects.get(
            template__category=self.family, key="couleurs_impression"
        )
        field.required = True
        field.save(update_fields=["required"])

        self.client_api.force_authenticate(self.buyer)
        ok = self.client_api.post(
            reverse("api_rfq"),
            {
                "supplierId": self.supplier.id,
                "categoryId": self.box.id,
                "name": "Amina",
                "email": "amina@example.com",
                "quantity": 500,
                "specs": self._specs(),
            },
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        missing = self.client_api.post(
            reverse("api_rfq"),
            {
                "supplierId": self.supplier.id,
                "categoryId": self.box.id,
                "name": "Amina",
                "email": "amina@example.com",
                "quantity": 500,
                "specs": self._specs(impression="Quadrichromie"),
            },
            format="json",
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("couleurs", missing.data["error"])

    def test_snapshot_survives_later_form_edits(self):
        self.client_api.force_authenticate(self.buyer)
        response = self.client_api.post(
            reverse("api_rfq"),
            {
                "supplierId": self.supplier.id,
                "categoryId": self.box.id,
                "name": "Amina",
                "email": "amina@example.com",
                "quantity": 500,
                "specs": self._specs(longueur=210),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        rfq = RFQRequest.objects.get(pk=response.data["rfqId"])
        stored = rfq.specs_snapshot["fields"]
        length = next(row for row in stored if row["key"] == "longueur")
        self.assertEqual(length["label"], "Longueur")
        self.assertEqual(length["value"], 210)
        self.assertNotIn("cannelure", {row["key"] for row in stored})

        QuoteFormField.objects.filter(key="longueur").update(label_fr="Longueur intérieure")
        template = QuoteFormTemplate.objects.get(category=self.family)
        template.version = 9
        template.save(update_fields=["version"])
        rfq.refresh_from_db()
        length = next(
            row for row in rfq.specs_snapshot["fields"] if row["key"] == "longueur"
        )
        self.assertEqual(length["label"], "Longueur")
        self.assertEqual(rfq.specs_snapshot["template_version"], 1)
        self.assertTrue(
            rfq.events.filter(action=RFQRequestEvent.Action.SUBMITTED).exists()
        )

    def test_supplier_customizes_only_own_form(self):
        self.client_api.force_authenticate(self.owner)
        current = self.client_api.get(
            reverse("api_supplier_quote_form_detail", args=[self.box.id])
        )
        self.assertEqual(current.status_code, 200)
        fields = []
        for field in current.data["fields"]:
            row = {
                "key": field["key"],
                "enabled": field["key"] != "commentaire",
                "required": field["required"],
                "options": field["platformOptions"],
            }
            if field["key"] == "type_carton":
                row["options"] = ["Carton compact"]
            fields.append(row)
        saved = self.client_api.put(
            reverse("api_supplier_quote_form_detail", args=[self.box.id]),
            {
                "physicalCustomization": "paid",
                "buyerInstructions": "Joindre le tracé.",
                "fields": fields,
                "extraQuestions": [{
                    "label": "Référence interne",
                    "fieldType": "text",
                    "required": False,
                }],
            },
            format="json",
        )
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data["physicalCustomization"], "paid")

        locked = self.client_api.put(
            reverse("api_supplier_quote_form_detail", args=[self.box.id]),
            {
                "fields": [
                    {**row, "enabled": row["key"] != "longueur"}
                    for row in fields
                ],
                "extraQuestions": [],
            },
            format="json",
        )
        self.assertEqual(locked.status_code, 400)

        self.client_api.force_authenticate(self.other_user)
        denied = self.client_api.get(
            reverse("api_supplier_quote_form_detail", args=[self.box.id])
        )
        self.assertEqual(denied.status_code, 404)
        self.assertFalse(
            SupplierQuoteForm.objects.filter(supplier=self.other).exists()
        )

        self.client_api.force_authenticate(self.buyer)
        public = self.client_api.get(
            reverse("api_quote_form"),
            {"supplierId": self.supplier.id, "categoryId": self.box.id},
        )
        keys = [field["key"] for field in public.data["fields"]]
        self.assertNotIn("commentaire", keys)
        self.assertIn("extra_reference_interne", keys)
        carton = next(field for field in public.data["fields"] if field["key"] == "type_carton")
        self.assertEqual(carton["options"], ["Carton compact"])
        rejected = self.client_api.post(
            reverse("api_rfq"),
            {
                "supplierId": self.supplier.id,
                "categoryId": self.box.id,
                "name": "Amina",
                "email": "amina@example.com",
                "quantity": 10,
                "specs": self._specs(type_carton="Carton ondulé"),
            },
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)

    def test_supplier_reads_only_own_request_and_answers_it(self):
        self.client_api.force_authenticate(self.buyer)
        created = self.client_api.post(
            reverse("api_rfq"),
            {
                "supplierId": self.supplier.id,
                "categoryId": self.box.id,
                "name": "Amina",
                "email": "amina@example.com",
                "quantity": 800,
                "specs": json.dumps(self._specs()),
                "files": SimpleUploadedFile(
                    "plan.pdf", b"%PDF-1.4", content_type="application/pdf"
                ),
            },
            format="multipart",
        )
        self.assertEqual(created.status_code, 200, created.data)
        rfq_id = created.data["rfqId"]

        self.client_api.force_authenticate(self.other_user)
        hidden = self.client_api.get(reverse("api_supplier_rfq_detail", args=[rfq_id]))
        self.assertEqual(hidden.status_code, 404)

        self.client_api.force_authenticate(self.owner)
        detail = self.client_api.get(reverse("api_supplier_rfq_detail", args=[rfq_id]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["categoryName"], "Boîte pliante")
        self.assertEqual(detail.data["quantityUnit"], "pièces")
        self.assertTrue(any(row["key"] == "longueur" for row in detail.data["specs"]))
        self.assertEqual(len(detail.data["files"]), 1)

        blocked = self.client_api.post(
            reverse("api_supplier_rfq_status", args=[rfq_id]),
            {"status": "accepted"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 409)
        reviewed = self.client_api.post(
            reverse("api_supplier_rfq_status", args=[rfq_id]),
            {"status": "in_review", "comment": "Dossier reçu."},
            format="json",
        )
        self.assertEqual(reviewed.status_code, 200)

        quoted = self.client_api.post(
            reverse("api_supplier_rfq_respond", args=[rfq_id]),
            {
                "message": "Offre ferme.",
                "proposedPrice": "42.50",
                "moq": 500,
                "leadTimeDays": 15,
                "customizationFee": "12000",
                "commercialTerms": "Paiement à 30 jours.",
            },
            format="json",
        )
        self.assertEqual(quoted.status_code, 201, quoted.data)
        self.assertEqual(quoted.data["moq"], 500)
        self.assertEqual(quoted.data["commercialTerms"], "Paiement à 30 jours.")
        rfq = RFQRequest.objects.get(pk=rfq_id)
        self.assertEqual(rfq.status, RFQRequest.Status.QUOTED)
        self.assertTrue(rfq.events.filter(action=RFQRequestEvent.Action.QUOTED).exists())

    def test_buyer_page_lists_the_supplier_form(self):
        self.client.force_login(self.buyer)
        page = self.client.get(reverse("request_quote", args=[self.supplier.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Demander un devis")
        typed = self.client.get(
            reverse("request_quote", args=[self.supplier.id]),
            {"category": self.box.id},
        )
        self.assertContains(typed, "Longueur")
        self.assertContains(typed, "data-condition-field")
        profile = self.client.get(reverse("supplier_detail", args=[self.supplier.id]))
        self.assertContains(profile, "Demander un devis")
