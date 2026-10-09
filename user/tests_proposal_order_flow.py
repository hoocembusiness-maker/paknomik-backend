"""Quote demand → proposal → revision → acceptance → order → execution tracking.

Every step of this flow must stay free: no Pragmadinar debit, hold or ledger entry.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from user import entitlements
from user.models import (
    Category,
    Order,
    OrderStatusEvent,
    PragmadinarLedger,
    Product,
    QuoteDemand,
    QuoteDemandSelection,
    QuoteProposal,
    QuoteProposalEvent,
)
from user.pragmadinar import credit_wallet, get_or_create_wallet, seed_pragmadinar_catalog
from user.platform_config import seed_settings
from user.tests_crm_pragmadinar import (
    make_buyer,
    make_supplier,
    make_supplier_user,
)

PROPOSAL_PAYLOAD = {
    "lineItems": [
        {"description": "Carton double cannelure", "quantity": 100, "unitPrice": "50.00"},
        {"description": "Impression 2 couleurs", "quantity": 1, "unitPrice": "3000.00"},
    ],
    "commercialTerms": {"paymentTerms": "30 jours", "taxRatePercent": 19},
    "leadTimeDays": 10,
    "moq": 50,
    "message": "Offre valable 15 jours.",
}


class ProposalOrderFlowTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        seed_pragmadinar_catalog()
        seed_settings()
        self.category = Category.objects.create(name_en="Cartons", level=1)
        self.buyer = make_buyer("flow")
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)
        self.supplier = make_supplier("Flow Co", plan="genereux")
        self.supplier.interested_categories.add(self.category)
        self.supplier_user = make_supplier_user(self.supplier, email="flow@example.com")
        self.supplier_client = APIClient()
        self.supplier_client.force_authenticate(self.supplier_user)
        Product.objects.create(
            supplier=self.supplier,
            name="Carton",
            description="Carton",
            category=self.category,
            subcategory=self.category,
            price=Decimal("45.00"),
        )
        credit_wallet(self.supplier, 30)
        self.ledger_count = PragmadinarLedger.objects.count()

    def _assert_no_pragmadinar_movement(self):
        wallet = get_or_create_wallet(self.supplier)
        self.assertEqual(wallet.balance, Decimal("30"))
        self.assertEqual(wallet.held_total, Decimal("0"))
        self.assertEqual(PragmadinarLedger.objects.count(), self.ledger_count)

    def _private_demand(self):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="private",
            category=self.category,
            subcategory=self.category,
            quantity=100,
            description="Cartons imprimés",
            visible_on_market=False,
        )
        QuoteDemandSelection.objects.create(demand=demand, supplier=self.supplier)
        return demand

    def _respond(self, demand, payload=None):
        return self.supplier_client.post(
            reverse("api_supplier_quote_demand_respond", args=[demand.pk]),
            payload or PROPOSAL_PAYLOAD,
            format="json",
        )

    def _decide(self, demand, proposal_id, decision, **extra):
        return self.buyer_client.post(
            reverse("api_quote_demand_decide", args=[demand.pk]),
            {"proposalId": proposal_id, "decision": decision, **extra},
            format="json",
        )

    def _set_status(self, order_id, status, **extra):
        return self.supplier_client.patch(
            reverse("api_supplier_order_status", args=[order_id]),
            {"status": status, **extra},
            format="json",
        )

    def test_full_flow_is_free_and_traced(self):
        demand = self._private_demand()

        sent = self._respond(demand)
        self.assertEqual(sent.status_code, 201, sent.data)
        proposal = QuoteProposal.objects.get(demand=demand)
        self.assertEqual(proposal.status, QuoteProposal.Status.SENT)
        self.assertEqual(proposal.subtotal, Decimal("8000.00"))
        self.assertEqual(proposal.total_amount, Decimal("9520.00"))
        self.assertEqual(len(proposal.line_items), 2)

        locked = self._respond(demand)
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(locked.data["code"], "proposal_locked")

        missing_comment = self._decide(demand, proposal.pk, "request_revision")
        self.assertEqual(missing_comment.status_code, 400)
        revision = self._decide(
            demand, proposal.pk, "request_revision", comment="Baissez le prix unitaire."
        )
        self.assertEqual(revision.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, QuoteProposal.Status.REVISION_REQUESTED)

        draft = self._respond(demand, {**PROPOSAL_PAYLOAD, "saveAsDraft": True})
        self.assertEqual(draft.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, QuoteProposal.Status.REVISION_REQUESTED)

        revised_payload = {
            **PROPOSAL_PAYLOAD,
            "lineItems": [
                {"description": "Carton double cannelure", "quantity": 100, "unitPrice": "45"}
            ],
        }
        revised = self._respond(demand, revised_payload)
        self.assertEqual(revised.status_code, 200, revised.data)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, QuoteProposal.Status.SENT)
        self.assertEqual(proposal.version, 2)
        self.assertEqual(proposal.buyer_response_comment, "")
        actions = list(proposal.events.values_list("action", flat=True))
        self.assertIn(QuoteProposalEvent.Action.REVISION_REQUESTED, actions)
        self.assertIn(QuoteProposalEvent.Action.REVISED, actions)

        delivery_date = (timezone.localdate() + timedelta(days=20)).isoformat()
        accepted = self._decide(
            demand,
            proposal.pk,
            "accept",
            order={
                "deliveryAddress": "Zone industrielle, Sétif",
                "receiverContact": "M. Amine — 0550 00 00 00",
                "buyerPoReference": "PO-2026-17",
                "expectedDeliveryDate": delivery_date,
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertNotIn("orderError", accepted.data)
        order = Order.objects.get(pk=accepted.data["orderId"])
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.delivery_address, "Zone industrielle, Sétif")
        self.assertEqual(order.buyer_po_reference, "PO-2026-17")
        self.assertEqual(order.expected_delivery_date.isoformat(), delivery_date)
        self.assertEqual(order.commercial_snapshot["version"], 2)
        self.assertTrue(
            OrderStatusEvent.objects.filter(order=order, to_status="confirmed").exists()
        )
        demand.refresh_from_db()
        self.assertEqual(demand.status, QuoteDemand.Status.RESOLVED)

        skip = self._set_status(order.pk, "delivered")
        self.assertEqual(skip.status_code, 409)
        self.assertEqual(skip.data["code"], "invalid_transition")

        for step in ("in_production", "shipped", "delivered", "completed"):
            response = self._set_status(order.pk, step, note=f"Étape {step}")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["status"], step)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        history = [e.to_status for e in order.status_events.all()]
        self.assertEqual(
            history, ["confirmed", "in_production", "shipped", "delivered", "completed"]
        )

        buyer_orders = self.buyer_client.get(reverse("api_buyer_orders"))
        self.assertEqual(buyer_orders.status_code, 200)
        row = next(r for r in buyer_orders.data if r["id"] == order.pk)
        self.assertEqual(len(row["statusHistory"]), 5)
        self.assertEqual(row["quoteDemandId"], demand.pk)

        self._assert_no_pragmadinar_movement()

    def test_supplier_cancel_requires_reason_and_buyer_cannot_cancel_in_production(self):
        demand = self._private_demand()
        self._respond(demand)
        proposal = QuoteProposal.objects.get(demand=demand)
        order_id = self._decide(demand, proposal.pk, "accept").data["orderId"]

        no_reason = self._set_status(order_id, "cancelled")
        self.assertEqual(no_reason.status_code, 400)
        self.assertEqual(no_reason.data["code"], "cancel_reason_required")

        def buyer_row():
            orders = self.buyer_client.get(reverse("api_buyer_orders"))
            return next(r for r in orders.data if r["id"] == order_id)

        self.assertTrue(buyer_row()["canCancel"])
        self.assertEqual(self._set_status(order_id, "in_production").status_code, 200)
        self.assertFalse(buyer_row()["canCancel"])
        cancel = self.buyer_client.patch(
            reverse("api_buyer_order_detail", args=[order_id]),
            {"action": "cancel"},
            format="json",
        )
        self.assertEqual(cancel.status_code, 409)
        self.assertEqual(cancel.data["code"], "order_not_cancellable")
        self._assert_no_pragmadinar_movement()

    def test_genereux_supplier_answers_public_demand_for_free(self):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="public",
            category=self.category,
            subcategory=self.category,
            quantity=100,
            description="Demande publique",
            visible_on_market=True,
            market_published_at=timezone.now(),
        )
        opportunities = self.supplier_client.get(reverse("api_supplier_opportunities"))
        self.assertEqual(opportunities.data["opportunityCostPragmadinar"], "0")
        row = next(r for r in opportunities.data["results"] if r["id"] == demand.pk)
        self.assertTrue(row["canRespondOpportunity"])

        response = self._respond(demand)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(demand.engagements.get().source, "public_joined")
        self._assert_no_pragmadinar_movement()

    def test_refusing_one_public_proposal_keeps_demand_open(self):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="public",
            category=self.category,
            subcategory=self.category,
            quantity=100,
            description="Demande publique",
            visible_on_market=True,
            market_published_at=timezone.now(),
        )
        self._respond(demand)
        proposal = QuoteProposal.objects.get(demand=demand)
        refused = self._decide(demand, proposal.pk, "refuse", comment="Prix trop élevé")
        self.assertEqual(refused.status_code, 200)
        proposal.refresh_from_db()
        demand.refresh_from_db()
        self.assertEqual(proposal.status, QuoteProposal.Status.LOST)
        self.assertEqual(demand.status, QuoteDemand.Status.OPEN)

    def test_drafts_are_hidden_from_buyer(self):
        demand = self._private_demand()
        draft = self._respond(demand, {**PROPOSAL_PAYLOAD, "saveAsDraft": True})
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(QuoteProposal.objects.get(demand=demand).status, "draft")
        detail = self.buyer_client.get(reverse("api_quote_demand_detail", args=[demand.pk]))
        self.assertEqual(detail.data["proposals"], [])
