from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from user import rbac
from user.api.serializers import SupplierProductFormSerializer
from user.models import (
    Category,
    CategoryAttribute,
    ProductAttributeValue,
    TechnicalAttribute,
    TechnicalAttributeProposal,
)

User = get_user_model()


class SupplierAttributeProposalTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_en="Bottles", name_fr="Bouteilles", level=1)
        self.subcategory = Category.objects.create(
            name_en="PET bottle",
            name_fr="Bouteille PET",
            parent=self.category,
            level=2,
        )
        from user.models import Supplier

        self.supplier = Supplier.objects.create(company_name="Atlas Pack", email="atlas@example.com")
        self.other = Supplier.objects.create(company_name="Autre", email="autre@example.com")
        self.thickness = TechnicalAttribute.objects.create(
            name_en="Thickness",
            name_fr="Épaisseur",
            attribute_type="number",
        )
        CategoryAttribute.objects.create(category=self.subcategory, attribute=self.thickness)

    def _save(self, supplier, proposed, name="Bouteille"):
        serializer = SupplierProductFormSerializer(
            data={
                "name": name,
                "description": name,
                "categoryId": self.category.id,
                "subcategoryId": self.subcategory.id,
                "minOrderQuantity": 10,
                "leadTimeDays": 7,
                "listingType": "sell",
                "technicalAttributes": [],
                "proposedAttributes": proposed,
            },
            context={"supplier_id": supplier.id, "search_tag_limit": 64},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.save()

    def test_supplier_proposal_stays_private_until_an_admin_generalises_it(self):
        product = self._save(
            self.supplier,
            [
                {
                    "name": "Résistance UV",
                    "attributeType": "text",
                    "value": "Haute",
                }
            ],
        )
        proposal = TechnicalAttribute.objects.get(name_fr="Résistance UV")
        self.assertEqual(proposal.proposal.status, TechnicalAttributeProposal.PENDING)
        self.assertEqual(proposal.proposal.supplier_id, self.supplier.id)
        self.assertFalse(CategoryAttribute.objects.filter(attribute=proposal).exists())
        self.assertTrue(
            ProductAttributeValue.objects.filter(product=product, attribute=proposal, txt_value="Haute").exists()
        )

        client = APIClient()
        listed = client.get(f"/api/category-attributes/?subcategory={self.subcategory.id}").json()
        self.assertNotIn("Résistance UV", [row["nameFr"] for row in listed])

        from user.attribute_proposals import generalize_proposal

        generalize_proposal(
            proposal.proposal,
            name="Résistance UV",
            attribute_type="text",
            unit_symbol="",
        )
        proposal.refresh_from_db()
        self.assertEqual(proposal.proposal.status, TechnicalAttributeProposal.GENERALIZED)
        self.assertTrue(
            CategoryAttribute.objects.filter(category=self.subcategory, attribute=proposal).exists()
        )
        listed = client.get(f"/api/category-attributes/?subcategory={self.subcategory.id}").json()
        self.assertIn("Résistance UV", [row["nameFr"] for row in listed])

    def test_another_supplier_cannot_reuse_a_private_attribute(self):
        product = self._save(
            self.supplier,
            [{"name": "Opacité", "attributeType": "text", "value": "Laiteuse"}],
        )
        proposal = TechnicalAttribute.objects.get(name_fr="Opacité")
        other_product = self._save(
            self.other,
            [],
            name="Autre bouteille",
        )
        from user.api.serializers import _save_product_technical_attributes

        _save_product_technical_attributes(
            other_product,
            self.category,
            self.subcategory,
            [{"attributeId": proposal.id, "value": "Laiteuse"}],
        )
        self.assertFalse(
            ProductAttributeValue.objects.filter(product=other_product, attribute=proposal).exists()
        )
        self.assertTrue(
            ProductAttributeValue.objects.filter(product=product, attribute=proposal).exists()
        )

    def test_known_catalogue_name_reuses_the_catalogue_attribute(self):
        product = self._save(
            self.supplier,
            [{"name": "Épaisseur", "attributeType": "number", "unitSymbol": "µm", "value": "20"}],
        )
        self.assertFalse(
            TechnicalAttribute.objects.filter(
                proposal__supplier=self.supplier, name_fr__iexact="Épaisseur"
            ).exists()
        )
        self.assertTrue(
            ProductAttributeValue.objects.filter(product=product, attribute=self.thickness).exists()
        )

    def test_admin_queue_generalise_and_reject(self):
        rbac.seed_roles()
        staff = User.objects.create_user(
            username="ops",
            email="ops@example.com",
            password="x-pass-123",
            is_staff=True,
        )
        rbac.grant_role(staff, rbac.ADMIN_OPERATIONS)
        outsider = User.objects.create_user(
            username="support",
            email="support@example.com",
            password="x-pass-123",
            is_staff=True,
        )
        rbac.grant_role(outsider, rbac.ADMIN_SUPPORT)

        self._save(
            self.supplier,
            [{"name": "Résistance UV", "attributeType": "text", "value": "Haute"}],
        )
        proposal = TechnicalAttribute.objects.get(name_fr="Résistance UV")
        client = APIClient()
        client.force_authenticate(outsider)
        self.assertEqual(client.get("/api/admin/technical-attribute-proposals/").status_code, 403)

        client.force_authenticate(staff)
        queue = client.get("/api/admin/technical-attribute-proposals/").json()
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["results"][0]["name"], "Résistance UV")
        self.assertEqual(queue["results"][0]["supplierName"], "Atlas Pack")

        rejected = client.post(
            f"/api/admin/technical-attribute-proposals/{proposal.id}/",
            {"action": "reject"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.proposal.status, TechnicalAttributeProposal.REJECTED)
        self.assertEqual(
            client.get("/api/admin/technical-attribute-proposals/").json()["count"],
            0,
        )

        restored = client.post(
            f"/api/admin/technical-attribute-proposals/{proposal.id}/",
            {
                "action": "generalize",
                "name": "Tenue aux UV",
                "attributeType": "text",
                "unitSymbol": "",
            },
            format="json",
        )
        self.assertEqual(restored.status_code, 200, restored.content)
        proposal.refresh_from_db()
        self.assertEqual(proposal.name_fr, "Tenue aux UV")
        self.assertEqual(proposal.proposal.status, TechnicalAttributeProposal.GENERALIZED)
        self.assertTrue(
            CategoryAttribute.objects.filter(category=self.subcategory, attribute=proposal).exists()
        )
