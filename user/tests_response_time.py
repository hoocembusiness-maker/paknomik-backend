from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from user.models import (
    Category,
    Message,
    QuoteDemand,
    QuoteDemandSelection,
    QuoteProposal,
    QuoteProposalEvent,
    QuoteResponse,
    RFQRequest,
    SampleRequest,
)
from user.response_time import format_response_time, response_delays
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user


class ResponseTimeTests(TestCase):
    def setUp(self):
        self.supplier = make_supplier("Delay Pack")
        self.supplier_user = make_supplier_user(self.supplier)
        self.t0 = timezone.now() - timedelta(days=2)

    def _at(self, minutes):
        return self.t0 + timedelta(minutes=minutes)

    def _message(self, buyer, sender, minutes):
        message = Message.objects.create(
            supplier=self.supplier, buyer=buyer, sender=sender, content="Bonjour"
        )
        Message.objects.filter(pk=message.pk).update(created_at=self._at(minutes))

    def _sample(self, minutes, *, buyer=None, responded=None):
        sample = SampleRequest.objects.create(
            supplier=self.supplier,
            buyer=buyer,
            name="Amel",
            delivery_address="Alger",
            quantity=5,
            responded_at=self._at(responded) if responded is not None else None,
        )
        SampleRequest.objects.filter(pk=sample.pk).update(created_at=self._at(minutes))
        return sample

    def _delays(self):
        return sorted(round(m) for m in response_delays([self.supplier.pk])[self.supplier.pk])

    def test_all_request_kinds_count(self):
        buyer_msg, buyer_rfq, buyer_quote = make_buyer("m"), make_buyer("r"), make_buyer("q")
        self._message(buyer_msg, buyer_msg, 0)
        self._message(buyer_msg, self.supplier_user, 120)

        self._sample(0, responded=240)

        rfq = RFQRequest.objects.create(supplier=self.supplier, buyer=buyer_rfq, name="Rym")
        RFQRequest.objects.filter(pk=rfq.pk).update(created_at=self._at(0))
        quote = QuoteResponse.objects.create(quote_request=rfq, supplier=self.supplier, message="Devis")
        QuoteResponse.objects.filter(pk=quote.pk).update(created_at=self._at(360))

        category = Category.objects.create(name_en="Boxes", level=1)
        subcategory = Category.objects.create(name_en="Carton", level=2, parent=category)
        demand = QuoteDemand.objects.create(
            buyer=buyer_quote,
            kind=QuoteDemand.Kind.PRIVATE,
            category=category,
            subcategory=subcategory,
            quantity=1000,
            description="Cartons",
        )
        selection = QuoteDemandSelection.objects.create(demand=demand, supplier=self.supplier)
        QuoteDemandSelection.objects.filter(pk=selection.pk).update(created_at=self._at(0))
        proposal = QuoteProposal.objects.create(
            demand=demand,
            supplier=self.supplier,
            unit_price=Decimal("10"),
            confirmed_quantity=1000,
            lead_time_days=5,
            moq=100,
            sent_at=self._at(900),
        )
        event = QuoteProposalEvent.objects.create(
            proposal=proposal, action=QuoteProposalEvent.Action.SENT
        )
        QuoteProposalEvent.objects.filter(pk=event.pk).update(created_at=self._at(480))

        self.assertEqual(self._delays(), [120, 240, 360, 480])
        data = APIClient().get(f"/api/suppliers/{self.supplier.pk}/").data
        self.assertEqual(data["responseTime"], "5h")

    def test_request_with_automatic_message_counts_once(self):
        buyer = make_buyer("s")
        self._sample(0, buyer=buyer, responded=60)
        self._message(buyer, buyer, 0)
        self.assertEqual(self._delays(), [60])

    def test_unanswered_and_draft_replies_are_ignored(self):
        self._sample(0)
        rfq = RFQRequest.objects.create(supplier=self.supplier, name="Rym")
        QuoteResponse.objects.create(
            quote_request=rfq,
            supplier=self.supplier,
            message="Brouillon",
            status=QuoteResponse.Status.DRAFT,
        )
        self.assertEqual(self._delays(), [])
        data = APIClient().get(f"/api/suppliers/{self.supplier.pk}/").data
        self.assertIsNone(data["responseTime"])

    def test_first_supplier_status_change_sets_responded_at_once(self):
        sample = self._sample(0)
        client = APIClient()
        client.force_authenticate(self.supplier_user)
        url = reverse("api_supplier_sample_request_status", args=[sample.pk])
        self.assertEqual(client.post(url, {"status": "in_review"}).status_code, 200)
        sample.refresh_from_db()
        first = sample.responded_at
        self.assertIsNotNone(first)
        self.assertEqual(client.post(url, {"status": "accepted"}).status_code, 200)
        sample.refresh_from_db()
        self.assertEqual(sample.responded_at, first)

    def test_public_format_rounds_up(self):
        self.assertIsNone(format_response_time(None))
        self.assertEqual(format_response_time(5), "1h")
        self.assertEqual(format_response_time(61), "2h")
        self.assertEqual(format_response_time(24 * 60), "24h")
        self.assertEqual(format_response_time(25 * 60), "2 j")
