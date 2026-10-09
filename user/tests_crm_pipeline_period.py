from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from user import crm, entitlements
from user.models import Category, Opportunity, Order, PragmadinarLedger, Product
from user.platform_config import pragmadinar_to_dzd
from user.pragmadinar import get_or_create_wallet
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user


class CrmPipelinePeriodTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        self.category = Category.objects.create(name_en="Boxes", name_fr="Boîtes", slug="pipe-boxes")
        self.supplier = make_supplier("Pipe Co")
        self.client = APIClient()
        self.client.force_authenticate(make_supplier_user(self.supplier))
        self.buyer = make_buyer("pipe")
        self.product = Product.objects.create(
            supplier=self.supplier, name="Coffret", category=self.category
        )

    def _order(self, **extra):
        return Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=self.product,
            quantity=1000,
            unit_price=Decimal("8.00"),
            **extra,
        )

    def _pipeline(self, **params):
        response = self.client.get(reverse("api_crm_pipeline"), params)
        self.assertEqual(response.status_code, 200)
        return response.data

    def _ids(self, payload):
        return {item["id"] for column in payload["columns"] for item in column["items"]}

    def test_deal_is_dated_by_the_first_date_of_its_operation(self):
        order = self._order()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=40))
        order.refresh_from_db()
        old_operation = Opportunity.objects.create(
            supplier=self.supplier, title="Ancienne commande", stage="negotiation", order=order
        )
        fresh = Opportunity.objects.create(supplier=self.supplier, title="Nouvelle", stage="prospect")

        week = self._pipeline(period="7d")
        self.assertEqual(week["period"]["preset"], "7d")
        self.assertEqual(self._ids(week), {fresh.pk})

        start = (timezone.localdate() - timedelta(days=45)).isoformat()
        end = (timezone.localdate() - timedelta(days=35)).isoformat()
        window = self._pipeline(start=start, end=end)
        self.assertEqual(self._ids(window), {old_operation.pk})
        negotiation = next(c for c in window["columns"] if c["stage"] == "negotiation")
        item = negotiation["items"][0]
        self.assertEqual(item["startedAt"], order.created_at)

    def test_order_execution_moves_deal_to_won_and_counts_in_won_value(self):
        order = self._order()
        crm.sync_from_order(order)
        opportunity = crm.resolve_opportunity_for_order(order)
        self.assertEqual(opportunity.stage, Opportunity.Stage.REQUEST_RECEIVED)

        status_url = reverse("api_supplier_order_status", args=[order.pk])
        self.assertEqual(
            self.client.patch(status_url, {"status": "confirmed"}, format="json").status_code, 200
        )
        self.assertEqual(
            self.client.patch(status_url, {"status": "in_production"}, format="json").status_code,
            200,
        )
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage, Opportunity.Stage.WON)
        self.assertEqual(opportunity.estimated_value, Decimal("8000.00"))

        pipeline = self._pipeline(period="mtd")
        won = next(c for c in pipeline["columns"] if c["stage"] == "won")
        self.assertEqual(Decimal(won["totalValue"]), Decimal("8000.00"))
        dashboard = self.client.get(reverse("api_supplier_crm_dashboard"), {"period": "mtd"}).data
        self.assertEqual(Decimal(dashboard["month"]["wonValue"]), Decimal("8000.00"))
        self.assertEqual(dashboard["pipeline"]["won"]["count"], 1)

        profitability = dashboard["profitability"]
        self.assertIsNone(profitability["potential"])
        self.assertIsNone(profitability["cumulative"])
        self.assertEqual(Decimal(profitability["real"]["margin"]), Decimal("8000.00"))
        self.assertIsNone(profitability["real"]["roi"])

        cancel = self.client.patch(
            status_url, {"status": "cancelled", "note": "Client injoignable"}, format="json"
        )
        self.assertEqual(cancel.status_code, 200)
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage, Opportunity.Stage.LOST)
        self.assertEqual(opportunity.lost_reason, "Client injoignable")

    def test_dashboard_margin_and_roi_subtract_pragmadinar_consumed(self):
        Opportunity.objects.create(
            supplier=self.supplier,
            title="Gagnée",
            stage=Opportunity.Stage.WON,
            estimated_value=Decimal("5000"),
            closed_at=timezone.now(),
        )
        Opportunity.objects.create(
            supplier=self.supplier,
            title="Devis",
            stage=Opportunity.Stage.QUOTE_SENT,
            estimated_value=Decimal("3000"),
        )
        PragmadinarLedger.objects.create(
            wallet=get_or_create_wallet(self.supplier, "visibility"),
            entry_type=PragmadinarLedger.EntryType.SPEND,
            amount=Decimal("-10"),
            reason="test",
        )
        consumed_dzd = pragmadinar_to_dzd(Decimal("10"))

        data = self.client.get(reverse("api_supplier_crm_dashboard"), {"period": "mtd"}).data
        profitability = data["profitability"]

        self.assertEqual(Decimal(profitability["consumedDzd"]), consumed_dzd)
        real = profitability["real"]
        self.assertEqual(Decimal(real["margin"]), Decimal("5000") - consumed_dzd)
        self.assertEqual(
            real["roi"], round(float((Decimal("5000") - consumed_dzd) / consumed_dzd * 100), 1)
        )
        potential = profitability["potential"]
        self.assertEqual(Decimal(potential["amount"]), Decimal("3000"))
        self.assertEqual(Decimal(potential["margin"]), Decimal("3000") - consumed_dzd)
        cumulative = profitability["cumulative"]
        self.assertEqual(Decimal(cumulative["amount"]), Decimal("8000"))
        self.assertEqual(Decimal(cumulative["margin"]), Decimal("8000") - consumed_dzd)

        details = {row["title"]: row for row in cumulative["details"]}
        share = (consumed_dzd / 2).quantize(Decimal("0.01"))
        self.assertEqual(details["Gagnée"]["kind"], "real")
        self.assertEqual(details["Devis"]["kind"], "potential")
        self.assertEqual(Decimal(details["Devis"]["allocatedCost"]), share)
        self.assertEqual(Decimal(details["Devis"]["margin"]), Decimal("3000") - share)
        self.assertEqual(
            details["Devis"]["roi"], round(float((Decimal("3000") - share) / share * 100), 1)
        )
        self.assertEqual(
            sum(Decimal(row["margin"]) for row in details.values()),
            Decimal(cumulative["margin"]),
        )

    def test_open_deals_without_amount_keep_margin_unknown(self):
        Opportunity.objects.create(
            supplier=self.supplier, title="Devis sans montant", stage=Opportunity.Stage.QUOTE_SENT
        )
        potential = self._pipeline(period="mtd")["profitability"]["potential"]
        self.assertEqual(potential["count"], 1)
        self.assertEqual(potential["missingAmount"], 1)
        self.assertIsNone(potential["amount"])
        self.assertIsNone(potential["margin"])
        self.assertIsNone(potential["roi"])

    def test_pipeline_potential_includes_open_orders_without_estimate(self):
        order = self._order()
        Opportunity.objects.create(
            supplier=self.supplier, title="Commande", stage="negotiation", order=order
        )
        Opportunity.objects.create(
            supplier=self.supplier,
            title="Prospect",
            stage="prospect",
            estimated_value=Decimal("999"),
        )

        pipeline = self._pipeline(period="mtd")
        negotiation = next(c for c in pipeline["columns"] if c["stage"] == "negotiation")
        self.assertEqual(Decimal(negotiation["totalValue"]), Decimal("8000.00"))
        self.assertEqual(negotiation["items"][0]["estimatedValue"], "8000.00")
        profitability = pipeline["profitability"]
        self.assertEqual(Decimal(profitability["potential"]["amount"]), Decimal("8000.00"))
        self.assertIsNone(profitability["real"])
        self.assertIsNone(profitability["cumulative"])

    def test_dashboard_hides_margin_without_amount(self):
        data = self.client.get(reverse("api_supplier_crm_dashboard"), {"period": "mtd"}).data
        self.assertIsNone(data["profitability"]["real"])
        self.assertIsNone(data["profitability"]["potential"])
