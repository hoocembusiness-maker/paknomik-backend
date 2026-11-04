from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from buyer_space.catalogue_import import import_catalogue
from buyer_space.catalogue_models import (
    SuiteCompatibility,
    SuiteComponentDefinition,
    SuiteComponentLine,
    SuiteEnsemble,
    SuiteFavorite,
    SuiteImage,
    SuiteSource,
    SuiteSupplierOffer,
    SuiteTaxon,
    publish_blockers,
)
from user.models import Category, QuoteDemand
from user.tests_crm_pragmadinar import make_supplier

User = get_user_model()
WORKBOOK = Path(__file__).resolve().parent / "data" / "PakSuites_catalogue_initial.xlsx"


class CatalogueImportTests(TestCase):
    def test_workbook_imports_without_duplicates(self):
        if not WORKBOOK.exists():
            self.skipTest("Classeur initial absent")
        first = import_catalogue(WORKBOOK)
        second = import_catalogue(WORKBOOK)
        self.assertEqual(first.errors, [])
        self.assertEqual(second.errors, [])
        self.assertEqual(SuiteEnsemble.objects.count(), 25)
        self.assertEqual(second.ensembles_created, 0)
        self.assertEqual(SuiteEnsemble.objects.filter(editorial_status="published").count(), 25)
        self.assertGreater(SuiteComponentLine.objects.count(), 25)
        lotion = SuiteEnsemble.objects.get(code="PKS-COS-001")
        self.assertGreaterEqual(lotion.lines.count(), 3)
        self.assertTrue(lotion.sources.filter(code="S01").exists())
        pharma = SuiteEnsemble.objects.get(code="PKS-PHA-001")
        levels = set(pharma.lines.values_list("level", flat=True))
        self.assertTrue({"primary", "secondary", "tertiary"} <= levels)
        self.assertEqual(
            SuiteEnsemble.objects.filter(priority="P6", sectors__label_fr="Pharmacie").distinct().count(),
            8,
        )
        self.assertFalse(
            SuiteCompatibility.objects.filter(status=SuiteCompatibility.Status.CONFIRMED).exists()
        )
        self.assertEqual(SuiteEnsemble.objects.filter(code="PKS-COS-001").count(), 1)

    def test_search_filters_and_permissions(self):
        if not WORKBOOK.exists():
            self.skipTest("Classeur initial absent")
        import_catalogue(WORKBOOK)
        draft = SuiteEnsemble.objects.get(code="PKS-COS-003")
        draft.editorial_status = SuiteEnsemble.Status.DRAFT
        draft.save(update_fields=["editorial_status"])
        client = APIClient()
        listed = client.get("/api/paksuites/ensembles/?q=flacon")
        self.assertEqual(listed.status_code, 200)
        codes = [row["code"] for row in listed.json()["results"]]
        self.assertIn("PKS-COS-001", codes)
        self.assertNotIn("PKS-COS-003", codes)
        hidden = client.get("/api/paksuites/ensembles/pks-cos-003/")
        self.assertEqual(hidden.status_code, 404)
        sector = client.get("/api/paksuites/ensembles/?sector=Pharmacie&level=tertiary")
        self.assertEqual(sector.status_code, 200)
        self.assertGreater(sector.json()["count"], 0)
        detail = client.get("/api/paksuites/ensembles/pks-pha-001/").json()
        self.assertNotIn("internalNote", detail)
        self.assertTrue(any(line["level"] == "primary" for line in detail["lines"]))
        self.assertTrue(all(line["availability"] == "a_rechercher" for line in detail["lines"]))

    def test_missing_supplier_quote_and_favorite(self):
        ensemble = SuiteEnsemble.objects.create(
            code="PKS-TEST-001",
            name="Ensemble test",
            slug="pks-test-001",
            description="Concept documenté pour les tests.",
            confidence=SuiteEnsemble.Confidence.INFERRED,
            points_to_confirm="Col à confirmer.",
            sourced_facts="Note de test.",
            editorial_status=SuiteEnsemble.Status.PUBLISHED,
            function_label="Primaire",
        )
        definition = SuiteComponentDefinition.objects.create(code="flacon-test", label="Flacon test")
        line = SuiteComponentLine.objects.create(
            ensemble=ensemble,
            definition=definition,
            role="Contenant",
            level=SuiteComponentLine.Level.PRIMARY,
            status=SuiteComponentLine.Status.REQUIRED,
        )
        buyer = User.objects.create_user(
            username="suitebuyer", email="suitebuyer@example.com", password="Testpass!2345"
        )
        client = APIClient()
        client.force_authenticate(buyer)
        denied = client.post(f"/api/paksuites/lines/{line.id}/quote/", {"supplierId": 1}, format="json")
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.json()["code"], "availability_pending")
        self.assertEqual(QuoteDemand.objects.count(), 0)
        saved = client.post("/api/paksuites/ensembles/pks-test-001/favorite/")
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["saved"])
        self.assertEqual(SuiteFavorite.objects.filter(user=buyer).count(), 1)
        again = client.post("/api/paksuites/ensembles/pks-test-001/favorite/")
        self.assertFalse(again.json()["saved"])

    def test_quote_uses_existing_demand_when_offer_is_mapped(self):
        family = Category.objects.create(name_en="Contenants", name_fr="Contenants", level=1)
        sub = Category.objects.create(name_en="Flacon", name_fr="Flacon", parent=family, level=2)
        taxon = SuiteTaxon.objects.create(
            kind=SuiteTaxon.Kind.TYPE,
            code="flacon-map",
            label_fr="Flacon",
            parent=SuiteTaxon.objects.create(
                kind=SuiteTaxon.Kind.FAMILY, code="F-T", label_fr="Test"
            ),
            marketplace_category=sub,
        )
        ensemble = SuiteEnsemble.objects.create(
            code="PKS-TEST-002",
            name="Ensemble relié",
            slug="pks-test-002",
            description="Ensemble avec une offre.",
            sourced_facts="Offre de test.",
            points_to_confirm="Aucun point bloquant.",
            editorial_status=SuiteEnsemble.Status.PUBLISHED,
        )
        definition = SuiteComponentDefinition.objects.create(code="flacon-map", label="Flacon")
        line = SuiteComponentLine.objects.create(
            ensemble=ensemble,
            definition=definition,
            taxon=taxon,
            role="Contenant",
            status=SuiteComponentLine.Status.REQUIRED,
            level=SuiteComponentLine.Level.PRIMARY,
        )
        supplier = make_supplier("Suite Fournisseur")
        SuiteSupplierOffer.objects.create(line=line, supplier=supplier)
        buyer = User.objects.create_user(
            username="suitebuyer2", email="suitebuyer2@example.com", password="Testpass!2345", wilaya="oran"
        )
        client = APIClient()
        client.force_authenticate(buyer)
        created = client.post(
            f"/api/paksuites/lines/{line.id}/quote/",
            {"supplierId": supplier.id, "quantity": 10},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        demand = QuoteDemand.objects.get()
        self.assertEqual(demand.catalogue_line_id, line.id)
        self.assertEqual(demand.kind, QuoteDemand.Kind.PRIVATE)
        self.assertEqual(list(demand.selections.values_list("supplier_id", flat=True)), [supplier.id])
        self.assertTrue(created.json()["demand"]["fromPaksuites"])

    def test_unverified_image_is_not_presented_as_validated(self):
        ensemble = SuiteEnsemble.objects.create(
            code="PKS-TEST-003",
            name="Sans image",
            slug="pks-test-003",
            description="Pas de visuel validé.",
            sourced_facts="Source interne de test.",
            points_to_confirm="Droits à vérifier.",
            editorial_status=SuiteEnsemble.Status.PUBLISHED,
        )
        image = SuiteImage(
            ensemble=ensemble,
            remote_url="https://example.invalid/photo.jpg",
            rights=SuiteImage.Rights.TO_VERIFY,
            illustrative=False,
        )
        image.save()
        payload = APIClient().get("/api/paksuites/ensembles/pks-test-003/").json()
        self.assertEqual(payload["image"], None)
        self.assertEqual(payload["images"][0]["url"], None)
        self.assertNotIn("remoteUrl", payload["images"][0])
        self.assertEqual(payload["placeholder"], "")

    def test_confirmed_compatibility_requires_proof(self):
        ensemble = SuiteEnsemble.objects.create(
            code="PKS-TEST-004", name="Preuve", slug="pks-test-004", description="x"
        )
        definition = SuiteComponentDefinition.objects.create(code="preuve", label="Preuve")
        line = SuiteComponentLine.objects.create(ensemble=ensemble, definition=definition, role="Test")
        item = SuiteCompatibility(subject=line, status=SuiteCompatibility.Status.CONFIRMED)
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_staff_publish_guard_and_anonymous_admin(self):
        source = SuiteSource.objects.create(code="S99", name="Note")
        ensemble = SuiteEnsemble.objects.create(
            code="PKS-TEST-005",
            name="",
            slug="pks-test-005",
            description="",
            editorial_status=SuiteEnsemble.Status.DRAFT,
        )
        ensemble.sources.add(source)
        self.assertTrue(publish_blockers(ensemble))
        staff = User.objects.create_user(
            username="suitestaff",
            email="suitestaff@example.com",
            password="Testpass!2345",
            is_staff=True,
        )
        client = APIClient()
        self.assertIn(client.get("/api/paksuites/admin/ensembles/").status_code, (401, 403))
        client.force_authenticate(staff)
        refused = client.patch(
            "/api/paksuites/admin/ensembles/PKS-TEST-005/",
            {"editorialStatus": "published", "name": "Nom", "description": ""},
            format="json",
        )
        self.assertEqual(refused.status_code, 400)
        ensemble.refresh_from_db()
        self.assertEqual(ensemble.editorial_status, SuiteEnsemble.Status.DRAFT)

    def test_component_visual_is_shared_and_tracked(self):
        from buyer_space.suite_visuals import infer_visual_kind, production_status_for

        self.assertEqual(infer_visual_kind("flacon-en-plastique", "Flacon en plastique"), "flacon")
        self.assertEqual(infer_visual_kind("bouchon-a-vis-en-plastique", "Bouchon à vis"), "bouchon")
        self.assertEqual(infer_visual_kind("etiquette-auto-adhesive", "Étiquette"), "etiquette")
        self.assertEqual(infer_visual_kind("joint-type", "Joint"), "joint")
        self.assertEqual(production_status_for("flacon"), "produced_internal")
        self.assertEqual(production_status_for("joint"), "produced_internal")
        self.assertEqual(production_status_for("inconnu-xyz"), "to_produce")

        definition = SuiteComponentDefinition.objects.create(
            code="flacon-test-visuel",
            label="Flacon en plastique",
            visual_kind="flacon",
        )
        image = SuiteImage.objects.create(
            definition=definition,
            view_type="composant",
            production_status=SuiteImage.ProductionStatus.PRODUCED_INTERNAL,
            license=SuiteImage.License.PAKNOMIK,
            illustrative=True,
        )
        self.assertEqual(image.license, "paknomik")
        self.assertFalse(image.ensemble_id)
        self.assertEqual(
            SuiteImage.objects.filter(definition=definition).count(),
            1,
        )
