"""Compact local order, separate export request, export proposals and commission
tracking for orders born on Paknomik.

Run with: python manage.py test user.tests_order_export_commission
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from user import entitlements, rbac
from user.commission import register_order_commission
from user.models import (
    AuditLog,
    Category,
    Order,
    OrderCommission,
    PlatformRole,
    Product,
    QuoteDemand,
    QuoteDemandSelection,
    QuoteProposal,
    RoleGrant,
    SupplierMandate,
)
from user.pragmadinar import seed_pragmadinar_catalog
from user.platform_config import seed_settings, set_setting
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user

LOCAL_PROPOSAL = {
    "lineItems": [{"description": "Carton", "quantity": 100, "unitPrice": "50.00"}],
    "commercialTerms": {"validUntil": (date.today() + timedelta(days=10)).isoformat()},
    "leadTimeDays": 10,
    "message": "",
}


class OrderExportCommissionTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        seed_pragmadinar_catalog()
        seed_settings()
        rbac.seed_roles()
        self.category = Category.objects.create(name_en="Cartons", level=1)
        self.supplier = make_supplier("Export Co", plan="genereux")
        self.supplier.interested_categories.add(self.category)
        self.supplier_user = make_supplier_user(self.supplier, email="exp@example.com")
        self.supplier_client = APIClient()
        self.supplier_client.force_authenticate(self.supplier_user)
        self.product = Product.objects.create(
            supplier=self.supplier,
            name="Carton export",
            description="Carton",
            category=self.category,
            subcategory=self.category,
            price=Decimal("40.00"),
            min_order_quantity=10,
        )
        self.buyer = make_buyer("exp")
        self.buyer.wilaya = "algiers"
        self.buyer.save(update_fields=["wilaya"])
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)

    def _admin_client(self, suffix, role=rbac.ULTRA_ADMIN):
        user = make_buyer(suffix)
        RoleGrant.objects.create(user=user, role=PlatformRole.objects.get(code=role))
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _mandate(self, rate="6.00", **extra):
        return SupplierMandate.objects.create(
            supplier=self.supplier,
            reference="MAND-1",
            commission_rate=Decimal(rate),
            status=SupplierMandate.Status.ACTIVE,
            **extra,
        )

    def _order(self, client=None, **payload):
        body = {"productId": self.product.pk, "quantity": 20, **payload}
        return (client or self.buyer_client).post(reverse("api_orders"), body, format="json")

    # 1, 3, 10 — compact local order by a signed-in buyer
    def test_signed_in_buyer_orders_with_the_local_form(self):
        self._mandate()
        response = self._order(wilaya="algiers")
        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(pk=response.data["id"])
        self.assertEqual(order.origin, Order.Origin.PRODUCT_PAGE)
        self.assertEqual(order.trade_scope, "local")
        self.assertEqual(order.delivery_wilaya, "algiers")
        self.assertEqual(order.specifications, "")
        self.assertTrue(order.status_events.exists())
        self.assertTrue(
            AuditLog.objects.filter(action="order.created", entity_id=order.pk).exists()
        )

    # 2 — guest order with phone and dial code, no account
    def test_guest_submits_phone_and_order(self):
        guest = APIClient()
        response = self._order(
            client=guest, guestName="Karim", phoneCountryCode="+33", guestPhone="612 34 56 78"
        )
        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(pk=response.data["id"])
        self.assertIsNone(order.buyer)
        self.assertEqual(order.guest_phone_country_code, "+33")
        self.assertEqual(order.origin, Order.Origin.PRODUCT_PAGE)

    # 3, 4 — optional technical preference, limited server side, visible to the supplier
    def test_technical_preference_is_optional_limited_and_shown_to_supplier(self):
        too_long = self._order(wilaya="algiers", specifications="x" * 1001)
        self.assertEqual(too_long.status_code, 400)
        response = self._order(wilaya="algiers", specifications="Épaisseur 5 mm, kraft brun")
        self.assertEqual(response.status_code, 201, response.data)
        listing = self.supplier_client.get(reverse("api_supplier_orders"))
        self.assertEqual(listing.status_code, 200)
        rows = listing.data["results"] if isinstance(listing.data, dict) else listing.data
        row = next(r for r in rows if r["id"] == response.data["id"])
        self.assertEqual(row["specifications"], "Épaisseur 5 mm, kraft brun")

    # 5, 6 — distinct export request without forwarder nor Incoterm
    def test_export_request_needs_country_only(self):
        missing = self._order(tradeScope="export")
        self.assertEqual(missing.status_code, 400)
        home = self._order(tradeScope="export", destinationCountry="DZ")
        self.assertEqual(home.status_code, 400)
        response = self._order(
            tradeScope="export", destinationCountry="fr", destinationCity="Marseille"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("n'encaisse aucun paiement", response.data["message"])
        order = Order.objects.get(pk=response.data["id"])
        self.assertEqual(order.trade_scope, "export")
        self.assertEqual(order.destination_country, "FR")
        self.assertEqual(order.incoterm, "")
        self.assertEqual(order.delivery_wilaya, "")

    def test_export_request_rejects_unknown_incoterm(self):
        response = self._order(tradeScope="export", destinationCountry="ES", incoterm="XYZ")
        self.assertEqual(response.status_code, 400)
        ok = self._order(tradeScope="export", destinationCountry="ES", incoterm="TBD")
        self.assertEqual(ok.status_code, 201, ok.data)

    def _demand(self, **extra):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="private",
            category=self.category,
            subcategory=self.category,
            quantity=100,
            description="Cartons",
            visible_on_market=False,
            **extra,
        )
        QuoteDemandSelection.objects.create(demand=demand, supplier=self.supplier)
        return demand

    def _respond(self, demand, payload):
        return self.supplier_client.post(
            reverse("api_supplier_quote_demand_respond", args=[demand.pk]),
            payload,
            format="json",
        )

    # 7 — local proposal stays short: export keys are dropped
    def test_local_proposal_ignores_export_fields(self):
        demand = self._demand()
        payload = {
            **LOCAL_PROPOSAL,
            "commercialTerms": {**LOCAL_PROPOSAL["commercialTerms"], "incoterm": "FOB"},
        }
        response = self._respond(demand, payload)
        self.assertEqual(response.status_code, 201, response.data)
        terms = QuoteProposal.objects.get(demand=demand).commercial_terms
        self.assertNotIn("incoterm", terms)
        self.assertNotIn("tradeScope", terms)

    # 8, 9 — export proposal with Incoterm, place, transport, forwarder → frozen in the order
    def test_export_proposal_terms_are_kept_in_the_order(self):
        self._mandate()
        demand = self._demand(trade_scope="export", destination_country="FR")
        export_terms = {
            "tradeScope": "export",
            "destinationCountry": "FR",
            "incoterm": "FOB",
            "transportMode": "sea",
            "forwarderName": "Transit Med",
            "forwarderContact": "+33 4 00 00 00 00",
            "freightCost": "500",
            "freightIncluded": True,
            "shippingLeadDays": 12,
            "paymentTerms": "Virement à convenir entre les parties",
        }
        missing_place = self._respond(
            demand, {**LOCAL_PROPOSAL, "currency": "EUR", "commercialTerms": export_terms}
        )
        self.assertEqual(missing_place.status_code, 400)
        self.assertEqual(missing_place.data["code"], "incoterm_place_required")

        export_terms["incotermPlace"] = "Port d'Alger"
        sent = self._respond(
            demand, {**LOCAL_PROPOSAL, "currency": "EUR", "commercialTerms": export_terms}
        )
        self.assertEqual(sent.status_code, 201, sent.data)
        proposal = QuoteProposal.objects.get(demand=demand)
        self.assertEqual(proposal.currency, "EUR")
        self.assertEqual(proposal.total_amount, Decimal("5500.00"))

        accepted = self.buyer_client.post(
            reverse("api_quote_demand_decide", args=[demand.pk]),
            {"proposalId": proposal.pk, "decision": "accept"},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        order = Order.objects.get(pk=accepted.data["orderId"])
        self.assertEqual(order.origin, Order.Origin.QUOTE_DEMAND)
        self.assertEqual(order.trade_scope, "export")
        self.assertEqual(order.destination_country, "FR")
        self.assertEqual(order.incoterm, "FOB")
        terms = order.commercial_snapshot["commercialTerms"]
        self.assertEqual(terms["incotermPlace"], "Port d'Alger")
        self.assertEqual(terms["forwarderName"], "Transit Med")
        self.assertEqual(order.commercial_snapshot["currency"], "EUR")

        # The snapshot is never replaced by current catalogue values.
        self.product.price = Decimal("999.00")
        self.product.save(update_fields=["price"])
        order.refresh_from_db()
        self.assertEqual(order.commercial_snapshot["totals"]["totalAmount"], "5500.00")

        commission = order.commission
        self.assertEqual(commission.rate, Decimal("6.00"))
        self.assertEqual(commission.reference_amount, Decimal("5500.00"))
        self.assertEqual(commission.amount, Decimal("330.00"))
        self.assertEqual(commission.currency, "EUR")

        again = self.buyer_client.post(
            reverse("api_quote_demand_convert_to_order", args=[demand.pk]),
            {"proposalId": proposal.pk},
            format="json",
        )
        self.assertIn(again.status_code, (200, 201))
        self.assertEqual(Order.objects.filter(quote_proposal=proposal).count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(action="proposal.accepted", entity_id=proposal.pk).exists()
        )

    # 10, 11 — every Paknomik order is commissionable, whatever the supplier's tier
    def test_commission_does_not_depend_on_supplier_tier(self):
        self._mandate(rate="5.00")
        for tier in ("Classic", "Gold"):
            self.supplier.subscription_type = tier
            self.supplier.save(update_fields=["subscription_type"])
            response = self._order(wilaya="algiers")
            self.assertEqual(response.status_code, 201, response.data)
            commission = OrderCommission.objects.get(order_id=response.data["id"])
            self.assertEqual(commission.status, OrderCommission.Status.CALCULATED)
            self.assertEqual(commission.amount, Decimal("40.00"))

    def test_rate_is_frozen_when_the_mandate_changes(self):
        mandate = self._mandate(rate="6.00")
        response = self._order(wilaya="algiers")
        mandate.commission_rate = Decimal("8.00")
        mandate.save(update_fields=["commission_rate"])
        commission = OrderCommission.objects.get(order_id=response.data["id"])
        self.assertEqual(commission.rate, Decimal("6.00"))

    # 12 — external sales are never attributed automatically
    def test_external_order_gets_no_commission(self):
        self._mandate()
        order = Order.objects.create(
            supplier=self.supplier, product=self.product, quantity=10, unit_price=Decimal("40")
        )
        self.assertEqual(order.origin, Order.Origin.EXTERNAL)
        self.assertIsNone(register_order_commission(order))
        self.assertFalse(OrderCommission.objects.filter(order=order).exists())

    # 13 — no mandate: the order is recorded, the commission is flagged « à valider »
    def test_missing_mandate_flags_commission_to_validate(self):
        response = self._order(wilaya="algiers")
        self.assertEqual(response.status_code, 201)
        commission = OrderCommission.objects.get(order_id=response.data["id"])
        self.assertEqual(commission.status, OrderCommission.Status.TO_VALIDATE)
        self.assertIsNone(commission.amount)
        self.assertIsNone(commission.rate)

    def test_rate_outside_configured_range_is_flagged(self):
        set_setting("commission_rate_max_percent", "8")
        self._mandate(rate="9.00")
        response = self._order(wilaya="algiers")
        commission = OrderCommission.objects.get(order_id=response.data["id"])
        self.assertEqual(commission.status, OrderCommission.Status.TO_VALIDATE)
        self.assertIsNone(commission.amount)

    # 14 — nothing is ever asked of, nor shown to, the buyer
    def test_buyer_never_sees_commission(self):
        self._mandate()
        response = self._order(wilaya="algiers")
        for key in ("commission", "commissionAmount", "commissionRate"):
            self.assertNotIn(key, response.data)
        orders = self.buyer_client.get(reverse("api_buyer_orders"))
        self.assertEqual(orders.status_code, 200)
        row = next(r for r in orders.data if r["id"] == response.data["id"])
        self.assertNotIn("commission", row)
        supplier_rows = self.supplier_client.get(reverse("api_supplier_orders")).data
        supplier_rows = (
            supplier_rows["results"] if isinstance(supplier_rows, dict) else supplier_rows
        )
        supplier_row = next(r for r in supplier_rows if r["id"] == response.data["id"])
        self.assertEqual(supplier_row["commission"]["rate"], "6.00")
        response = self.buyer_client.get(reverse("api_supplier_commissions"))
        self.assertEqual(response.status_code, 403)

    # 15 — ownership and permissions are enforced server side
    def test_ownership_and_admin_permissions(self):
        self._mandate()
        response = self._order(wilaya="algiers")
        other_supplier = make_supplier("Other Co")
        other_user = make_supplier_user(other_supplier, email="other@example.com")
        other_client = APIClient()
        other_client.force_authenticate(other_user)
        rows = other_client.get(reverse("api_supplier_orders")).data
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertFalse(any(r["id"] == response.data["id"] for r in rows))
        self.assertEqual(
            other_client.get(reverse("api_supplier_commissions")).data["results"], []
        )
        other_buyer = APIClient()
        other_buyer.force_authenticate(make_buyer("intruder"))
        self.assertFalse(
            any(r["id"] == response.data["id"]
                for r in other_buyer.get(reverse("api_buyer_orders")).data)
        )

        commission = OrderCommission.objects.get(order_id=response.data["id"])
        url = reverse("api_admin_commission_detail", args=[commission.pk])
        response = self.supplier_client.get(reverse("api_admin_commissions"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.supplier_client.patch(url, {"status": "validated"}, format="json").status_code,
            403,
        )

        support_client = self._admin_client("support", rbac.ADMIN_SUPPORT)
        self.assertEqual(support_client.get(reverse("api_admin_commissions")).status_code, 403)
        operations = self._admin_client("ops", rbac.ADMIN_OPERATIONS)
        self.assertEqual(operations.get(reverse("api_admin_commissions")).status_code, 200)
        self.assertEqual(
            operations.patch(url, {"status": "validated"}, format="json").status_code, 403
        )

        ultra_client = self._admin_client("hocem")
        self.assertEqual(ultra_client.get(reverse("api_admin_commissions")).status_code, 200)
        validated = ultra_client.patch(
            url, {"status": "validated", "settlementStatus": "invoiced"}, format="json"
        )
        self.assertEqual(validated.status_code, 200, validated.data)
        self.assertEqual(validated.data["status"], "validated")
        self.assertTrue(
            AuditLog.objects.filter(action="commission.review", entity_id=commission.pk).exists()
        )

    def test_admin_attaches_a_mandate_to_a_commission_to_validate(self):
        response = self._order(wilaya="algiers")
        commission = OrderCommission.objects.get(order_id=response.data["id"])
        mandate = self._mandate(rate="7.00")
        client = self._admin_client("ultra2")
        reviewed = client.patch(
            reverse("api_admin_commission_detail", args=[commission.pk]),
            {"mandateId": mandate.pk},
            format="json",
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(reviewed.data["rate"], "7.00")
        self.assertEqual(reviewed.data["amount"], "56.00")
        self.assertEqual(reviewed.data["status"], "calculated")

    def test_mandate_rate_must_respect_configured_range(self):
        client = self._admin_client("ultra3")
        out = client.post(
            reverse("api_admin_mandates"),
            {"supplierId": self.supplier.pk, "commissionRate": "12"},
            format="json",
        )
        self.assertEqual(out.status_code, 400)
        self.assertEqual(out.data["code"], "rate_out_of_range")
        created = client.post(
            reverse("api_admin_mandates"),
            {"supplierId": self.supplier.pk, "commissionRate": "6.5", "status": "active"},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
