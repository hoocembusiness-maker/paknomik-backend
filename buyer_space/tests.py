from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from buyer_space.models import (
    BuyerAccount,
    BuyerPragmadinarEntry,
    PackagingItem,
    PackagingProject,
    ServiceAvailability,
    ServiceRequest,
)
from buyer_space.wallet import debit_for_service, grant_company_registration, remaining_for
from user.models import AppNotification, Category, QuoteDemand, RFQRequest, SampleRequest
from user.tests_crm_pragmadinar import make_supplier, make_supplier_user

User = get_user_model()


def make_simple_user(suffix, *, company=""):
    return User.objects.create_user(
        username=f"space{suffix}",
        email=f"space{suffix}@example.com",
        password="Testpass!2345",
        company_name=company or None,
    )


class DossierAndWalletTests(TestCase):
    def test_simple_user_gets_an_empty_dossier(self):
        user = make_simple_user("empty")
        account = BuyerAccount.objects.get(user=user)
        self.assertEqual(account.balance, Decimal("0"))
        self.assertFalse(account.entries.exists())

    def test_company_registration_grants_fifty_once_for_design_and_paklytik(self):
        user = make_simple_user("co")
        user.company_name = "Emballages Atlas"
        user.save(update_fields=["company_name"])
        self.assertTrue(grant_company_registration(user))
        self.assertFalse(grant_company_registration(user))
        account = user.buyer_account
        self.assertEqual(account.balance, Decimal("50"))
        self.assertEqual(remaining_for(account, "DESIGN"), Decimal("50"))
        self.assertEqual(remaining_for(account, "ANALYTICS"), Decimal("50"))
        self.assertEqual(remaining_for(account, "PAKSUITES"), Decimal("0"))
        self.assertEqual(remaining_for(account, "PAKMATH"), Decimal("0"))

    def test_supplier_registration_does_not_receive_the_buyer_grant(self):
        supplier = make_supplier("Fournisseur Grant")
        user = User.objects.create_user(
            username="seller-grant",
            email="seller-grant@example.com",
            password="Testpass!2345",
            company_name="Fournisseur Grant",
            supplier=supplier,
        )
        self.assertFalse(grant_company_registration(user))
        self.assertFalse(BuyerPragmadinarEntry.objects.filter(account__user=user).exists())

    def test_shared_grant_cannot_go_negative_and_is_idempotent(self):
        user = make_simple_user("spend", company="Atlas")
        grant_company_registration(user)
        account = user.buyer_account
        debit_for_service(
            account,
            "DESIGN",
            Decimal("50"),
            reason="Paknoma Design",
            idempotency_key="design-1",
        )
        self.assertEqual(remaining_for(account, "DESIGN"), Decimal("0"))
        self.assertEqual(remaining_for(account, "ANALYTICS"), Decimal("0"))
        with self.assertRaises(Exception):
            debit_for_service(
                account,
                "ANALYTICS",
                Decimal("1"),
                reason="Paklytik",
                idempotency_key="analytics-1",
            )
        again = debit_for_service(
            account,
            "DESIGN",
            Decimal("50"),
            reason="Paknoma Design",
            idempotency_key="design-1",
        )
        self.assertEqual(account.entries.filter(entry_type="spend").count(), 1)
        self.assertEqual(again.amount, Decimal("-50"))
        with self.assertRaises(ValidationError):
            again.delete()

    def test_profile_company_name_grants_once(self):
        user = make_simple_user("profile")
        client = APIClient()
        client.force_authenticate(user)
        response = client.patch(
            reverse("api_buyer_profile"),
            {"company": "Nouvelle société"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(user.buyer_account.balance, Decimal("50"))
        response = client.patch(
            reverse("api_buyer_profile"),
            {"company": "Société renommée"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(user.buyer_account.entries.count(), 1)


class IsolationTests(TestCase):
    def setUp(self):
        self.owner = make_simple_user("owner")
        self.other = make_simple_user("other")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other)

    def test_projects_are_scoped_to_the_user(self):
        created = self.client.post(reverse("api_buyer_projects"), {"name": "Biscuits"}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        project_id = created.data["id"]
        hidden = self.other_client.get(reverse("api_buyer_project_detail", args=[project_id]))
        self.assertEqual(hidden.status_code, 404)
        listing = self.other_client.get(reverse("api_buyer_projects"))
        self.assertEqual(listing.data["count"], 0)


class SpecVersionTests(TestCase):
    def test_technical_edits_increment_spec_version_notes_do_not(self):
        user = make_simple_user("spec")
        account = user.buyer_account
        project = PackagingProject.objects.create(
            account=account, name="Projet", created_by=user
        )
        item = PackagingItem.objects.create(project=project, name="Sachet", material="PE")
        self.assertEqual(item.spec_version, 1)
        item.notes = "à revoir"
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.spec_version, 1)
        item.material = "PP"
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.spec_version, 2)


class ServiceRequestTests(TestCase):
    def setUp(self):
        self.buyer = make_simple_user("svc")
        self.other = make_simple_user("svc-other")
        self.staff = User.objects.create_user(
            username="staff-svc",
            email="staff-svc@example.com",
            password="Testpass!2345",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.buyer)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)

    def test_availability_starts_on_request(self):
        response = self.client.get(reverse("api_buyer_service_availability"))
        self.assertEqual(response.status_code, 200, response.data)
        states = {row["serviceType"]: row["state"] for row in response.data}
        self.assertEqual(
            set(states),
            {"DESIGN", "ANALYTICS", "PAKSUITES", "PAKMATH"},
        )
        self.assertTrue(all(state == "available_on_request" for state in states.values()))
        self.assertEqual(
            next(row["label"] for row in response.data if row["serviceType"] == "PAKSUITES"),
            "PakSuites",
        )

    def test_each_payload_schema_rejects_unknown_fields(self):
        cases = {
            "DESIGN": {"product": "Biscuits", "style": "sobre"},
            "ANALYTICS": {"businessQuestion": "Quels formats se vendent ?"},
            "PAKSUITES": {"product": "Biscuits", "constraints": {"foodContact": True}},
            "PAKMATH": {"unitDimensions": {"length": 10, "width": 5, "height": 2, "unit": "cm"}},
        }
        for service_type, payload in cases.items():
            ok = self.client.post(
                reverse("api_buyer_service_requests"),
                {"serviceType": service_type, "title": service_type, "payload": payload},
                format="json",
            )
            self.assertEqual(ok.status_code, 201, ok.data)
            rejected = self.client.post(
                reverse("api_buyer_service_requests"),
                {
                    "serviceType": service_type,
                    "title": service_type,
                    "payload": {**payload, "unexpected": True},
                },
                format="json",
            )
            self.assertEqual(rejected.status_code, 400, rejected.data)

    def test_other_user_cannot_read_a_request(self):
        created = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "DESIGN", "title": "Logo", "payload": {"product": "Café"}},
            format="json",
        )
        other = APIClient()
        other.force_authenticate(self.other)
        hidden = other.get(reverse("api_buyer_service_request_detail", args=[created.data["id"]]))
        self.assertEqual(hidden.status_code, 404)

    def test_transitions_follow_the_allowed_path_and_notify(self):
        created = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "PAKMATH", "title": "Carton", "payload": {}},
            format="json",
        )
        request_id = created.data["id"]
        url = reverse("api_buyer_service_request_transition", args=[request_id])
        denied = self.client.post(url, {"status": "NEEDS_INFO"}, format="json")
        self.assertEqual(denied.status_code, 403)
        skipped = self.staff_client.post(url, {"status": "IN_PROGRESS"}, format="json")
        self.assertEqual(skipped.status_code, 409)
        self.assertEqual(
            self.staff_client.post(url, {"status": "NEEDS_INFO"}, format="json").status_code,
            200,
        )
        self.assertEqual(
            self.staff_client.post(url, {"status": "IN_PROGRESS"}, format="json").status_code,
            200,
        )
        back = self.staff_client.post(url, {"status": "NEEDS_INFO"}, format="json")
        self.assertEqual(back.status_code, 200, back.data)
        self.staff_client.post(url, {"status": "IN_PROGRESS"}, format="json")
        self.staff_client.post(url, {"status": "RESULT_READY"}, format="json")
        closed = self.staff_client.post(url, {"status": "CLOSED"}, format="json")
        self.assertEqual(closed.status_code, 200, closed.data)
        service_request = ServiceRequest.objects.get(pk=request_id)
        self.assertEqual(service_request.events.count(), 6)
        self.assertEqual(service_request.requested_by.app_notifications.filter(kind="service_request").count(), 6)

    def test_pakmath_deliverable_stays_indicative(self):
        created = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "PAKMATH", "title": "Calcul", "payload": {}},
            format="json",
        )
        response = self.staff_client.post(
            reverse("api_buyer_service_request_deliverables", args=[created.data["id"]]),
            {"label": "Arrangement", "result": {"carton": "40x30x20"}, "isIndicative": False},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["isIndicative"])
        self.assertIn("indicatifs", response.data["disclaimer"])

    def test_unavailable_service_rejects_creation(self):
        ServiceAvailability.objects.filter(service_type="DESIGN").update(
            state="coming_soon", message="Paknoma Design ouvre bientôt."
        )
        response = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "DESIGN", "title": "Trop tôt", "payload": {}},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("bientôt", response.data["error"])

    def test_buyer_attaches_a_request_to_an_owned_project(self):
        created = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "DESIGN", "title": "Étiquette", "payload": {"product": "Café"}},
            format="json",
        )
        project = PackagingProject.objects.create(
            account=self.buyer.buyer_account, name="Café", created_by=self.buyer
        )
        response = self.client.patch(
            reverse("api_buyer_service_request_detail", args=[created.data["id"]]),
            {"projectId": project.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["projectId"], project.id)

    def test_a_configured_tariff_is_debited_once_and_refused_when_short(self):
        self.buyer.company_name = "Atlas"
        self.buyer.save(update_fields=["company_name"])
        self.assertTrue(grant_company_registration(self.buyer))
        ServiceAvailability.objects.filter(service_type="DESIGN").update(unit_cost=Decimal("40"))
        first = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "DESIGN", "title": "Étiquette", "payload": {"product": "Café"}},
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["quotaCost"], "40.00")
        account = self.buyer.buyer_account
        self.assertEqual(account.balance, Decimal("10.00"))
        spend = account.entries.get(entry_type=BuyerPragmadinarEntry.EntryType.SPEND)
        self.assertEqual(spend.idempotency_key, f"service-request-{first.data['id']}")
        self.assertEqual(spend.service_request_id, first.data["id"])
        replay = debit_for_service(
            account,
            "DESIGN",
            Decimal("40"),
            reason="rejeu",
            idempotency_key=spend.idempotency_key,
            service_request=ServiceRequest.objects.get(pk=first.data["id"]),
        )
        self.assertEqual(replay.pk, spend.pk)
        self.assertEqual(account.entries.filter(entry_type="spend").count(), 1)
        second = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "DESIGN", "title": "Deuxième", "payload": {"product": "Thé"}},
            format="json",
        )
        self.assertEqual(second.status_code, 400, second.data)
        self.assertFalse(ServiceRequest.objects.filter(title="Deuxième").exists())
        self.assertEqual(account.balance, Decimal("10.00"))

    def test_a_missing_tariff_does_not_debit(self):
        self.buyer.company_name = "Atlas"
        self.buyer.save(update_fields=["company_name"])
        grant_company_registration(self.buyer)
        created = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "PAKMATH", "title": "Calcul", "payload": {}},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertIsNone(created.data["quotaCost"])
        self.assertEqual(self.buyer.buyer_account.balance, Decimal("50"))
        self.assertFalse(
            self.buyer.buyer_account.entries.filter(entry_type="spend").exists()
        )

    def test_journal_lists_entries_and_rejects_writes(self):
        self.buyer.company_name = "Atlas"
        self.buyer.save(update_fields=["company_name"])
        grant_company_registration(self.buyer)
        listed = self.client.get(reverse("api_buyer_entries"))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["count"], 1)
        self.assertEqual(Decimal(listed.data["balance"]), Decimal("50"))
        self.assertEqual(listed.data["results"][0]["entryType"], "company_grant")
        self.assertEqual(Decimal(listed.data["results"][0]["amount"]), Decimal("50"))
        denied = self.client.post(reverse("api_buyer_entries"), {}, format="json")
        self.assertEqual(denied.status_code, 405)

    def test_free_quota_is_used_before_the_tariff(self):
        ServiceAvailability.objects.filter(service_type="PAKMATH").update(
            unit_cost=Decimal("10"), free_quota=1
        )
        first = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "PAKMATH", "title": "Calcul inclus", "payload": {}},
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["quotaCost"], "0.00")
        self.assertFalse(self.buyer.buyer_account.entries.exists())
        second = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "PAKMATH", "title": "Calcul payant", "payload": {}},
            format="json",
        )
        self.assertEqual(second.status_code, 400, second.data)
        self.assertFalse(ServiceRequest.objects.filter(title="Calcul payant").exists())


class PaksuitesQuoteTests(TestCase):
    def setUp(self):
        self.buyer = make_simple_user("suite")
        self.staff = User.objects.create_user(
            username="staff-suite",
            email="staff-suite@example.com",
            password="Testpass!2345",
            is_staff=True,
        )
        family = Category.objects.create(name_en="Flex", name_fr="Souple", slug="suite-flex")
        child = Category.objects.create(
            name_en="Sachet",
            name_fr="Sachet",
            slug="suite-sachet",
            parent=family,
            level=2,
        )
        self.supplier_a = make_supplier("Alpha Pack", email="alpha-pack@example.com")
        self.supplier_b = make_supplier("Beta Pack", email="beta-pack@example.com")
        self.supplier_a.interested_subcategories.add(child)
        self.supplier_b.interested_subcategories.add(child)
        self.supplier_user_a = make_supplier_user(self.supplier_a, email="alpha-user@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.buyer)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)

    def _option(self):
        created = self.client.post(
            reverse("api_buyer_service_requests"),
            {"serviceType": "PAKSUITES", "title": "Solution biscuits", "payload": {"product": "Biscuits"}},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        option = self.staff_client.post(
            reverse("api_buyer_paksuites_options", args=[created.data["id"]]),
            {
                "title": "Sachet + carton",
                "components": [
                    {
                        "packagingType": "Sachet",
                        "description": "Sachet 200 g",
                        "suggestedSupplierId": self.supplier_a.id,
                    },
                    {
                        "packagingType": "Carton",
                        "description": "Carton de regroupement",
                        "suggestedSupplierId": self.supplier_b.id,
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(option.status_code, 201, option.data)
        return option.data["id"]

    def test_one_quote_per_component_without_duplication(self):
        option_id = self._option()
        first = self.client.post(reverse("api_buyer_paksuites_quotes", args=[option_id]), {}, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(len(first.data["quoteDemandIds"]), 2)
        self.assertEqual(QuoteDemand.objects.count(), 2)
        second = self.client.post(reverse("api_buyer_paksuites_quotes", args=[option_id]), {}, format="json")
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(QuoteDemand.objects.count(), 2)
        self.assertEqual(first.data["quoteDemandIds"], second.data["quoteDemandIds"])
        self.assertEqual(
            set(QuoteDemand.objects.values_list("selections__supplier_id", flat=True)),
            {self.supplier_a.id, self.supplier_b.id},
        )

    def test_supplier_sees_paksuites_origin_and_performance(self):
        option_id = self._option()
        self.client.post(reverse("api_buyer_paksuites_quotes", args=[option_id]), {}, format="json")
        supplier_client = APIClient()
        supplier_client.force_authenticate(self.supplier_user_a)
        response = supplier_client.get(reverse("api_supplier_quote_demands"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["paksuites"]["quoteRequestCount"], 1)
        self.assertEqual(response.data["paksuites"]["paksuitesCount"], 1)
        row = response.data["results"][0]
        self.assertTrue(row["fromPaksuites"])
        self.assertEqual(row["paksuitesOptionTitle"], "Sachet + carton")
        self.assertIn("PakSuites", row["banner"])
        notice = AppNotification.objects.get(user=self.supplier_user_a)
        self.assertIn("PakSuites", notice.title)
        self.assertIn("invention Paknomik", notice.body)


class ExistingFlowTests(TestCase):
    def test_sample_and_boutique_quote_still_work_without_project_links(self):
        supplier = make_supplier("Flux existant", email="flux@example.com")
        response = APIClient().post(
            reverse("api_sample_requests"),
            {
                "supplierId": supplier.id,
                "name": "Lina",
                "phone": "+213555000111",
                "deliveryAddress": "Alger",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        sample = SampleRequest.objects.get()
        self.assertIsNone(sample.packaging_project_id)
        self.assertIsNone(sample.packaging_item_id)
        rfq = RFQRequest.objects.create(supplier=supplier, name="Lina", email="lina@example.com")
        self.assertIsNotNone(rfq.pk)
        rfq_fields = {field.name for field in RFQRequest._meta.fields}
        self.assertFalse(rfq_fields & {"packaging_project", "packaging_item", "service_request", "paksuites_component"})
        demand_fields = {field.name for field in QuoteDemand._meta.fields}
        self.assertTrue(
            {"packaging_project", "packaging_item", "service_request", "paksuites_component"} <= demand_fields
        )

    def test_archiving_a_project_keeps_an_existing_quote(self):
        user = make_simple_user("archive")
        supplier = make_supplier("Archive Co", email="archive-co@example.com")
        project = PackagingProject.objects.create(account=user.buyer_account, name="Temporaire", created_by=user)
        demand = QuoteDemand.objects.create(
            buyer=user,
            kind=QuoteDemand.Kind.PRIVATE,
            category=Category.objects.create(name_en="Cat", slug="archive-cat"),
            subcategory=Category.objects.create(name_en="Sub", slug="archive-sub", parent_id=None),
            quantity=10,
            description="Devis déjà envoyé",
            packaging_project=project,
        )
        # subcategory must have a parent for the real flow; the row above is only a link fixture.
        demand.subcategory.parent = demand.category
        demand.subcategory.save(update_fields=["parent"])
        client = APIClient()
        client.force_authenticate(user)
        response = client.delete(reverse("api_buyer_project_detail", args=[project.id]))
        self.assertEqual(response.status_code, 200, response.data)
        demand.refresh_from_db()
        self.assertEqual(demand.packaging_project_id, project.id)
        project.refresh_from_db()
        self.assertEqual(project.status, PackagingProject.Status.ARCHIVED)

    def test_market_quote_from_a_packaging_item_stays_on_the_project(self):
        user = make_simple_user("fiche")
        family = Category.objects.create(name_en="Famille", name_fr="Famille", slug="fiche-famille")
        child = Category.objects.create(name_en="Sachet", name_fr="Sachet", slug="fiche-sachet", parent=family, level=2)
        project = PackagingProject.objects.create(account=user.buyer_account, name="Biscuits", created_by=user, need="Protéger")
        item = PackagingItem.objects.create(project=project, name="Sachet 200 g", target_quantity=500)
        client = APIClient()
        client.force_authenticate(user)
        created = client.post(
            reverse("api_quote_demands"),
            {
                "kind": "public",
                "categoryId": family.id,
                "subcategoryId": child.id,
                "quantity": 500,
                "description": "Sachet 200 g",
                "packagingItemId": item.id,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        demand = QuoteDemand.objects.get(pk=created.data["id"])
        self.assertEqual(demand.packaging_item_id, item.id)
        self.assertEqual(demand.packaging_project_id, project.id)
        self.assertEqual(created.data["demand"]["packagingProjectId"], project.id)
        self.assertEqual(created.data["demand"]["packagingSpecVersion"], 1)
        self.assertEqual(created.data["demand"]["currentSpecVersion"], 1)
        listed = client.get(reverse("api_quote_demands"))
        self.assertEqual(listed.data[0]["packagingItemId"], item.id)
        client.delete(reverse("api_buyer_project_detail", args=[project.id]))
        demand.refresh_from_db()
        self.assertEqual(demand.packaging_project_id, project.id)

    def test_a_changed_sheet_asks_the_buyer_to_revalidate_the_quote(self):
        user = make_simple_user("reval")
        family = Category.objects.create(name_en="Famille", name_fr="Famille", slug="reval-famille")
        child = Category.objects.create(
            name_en="Boîte", name_fr="Boîte", slug="reval-boite", parent=family, level=2
        )
        project = PackagingProject.objects.create(
            account=user.buyer_account, name="Café", created_by=user
        )
        item = PackagingItem.objects.create(
            project=project, name="Boîte 250 g", material="Carton", target_quantity=100
        )
        client = APIClient()
        client.force_authenticate(user)
        created = client.post(
            reverse("api_quote_demands"),
            {
                "kind": "public",
                "categoryId": family.id,
                "subcategoryId": child.id,
                "quantity": 100,
                "description": "Boîte 250 g",
                "packagingItemId": item.id,
            },
            format="json",
        )
        demand_id = created.data["demand"]["id"]
        item.material = "Carton recyclé"
        item.save()
        listed = client.get(reverse("api_quote_demands"))
        row = next(item_row for item_row in listed.data if item_row["id"] == demand_id)
        self.assertEqual(row["packagingSpecVersion"], 1)
        self.assertEqual(row["currentSpecVersion"], 2)
        confirmed = client.post(reverse("api_quote_demand_revalidate", args=[demand_id]))
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data["packagingSpecVersion"], 2)
        self.assertEqual(confirmed.data["currentSpecVersion"], 2)
        demand = QuoteDemand.objects.get(pk=demand_id)
        self.assertEqual(demand.packaging_spec_version, 2)
