"""Supplier dashboard geographic breakdown from event context."""

from datetime import date

from django.test import TestCase

from user.analytics import build_supplier_geo_breakdown
from user.models import Category, Event, Product, Supplier


class SupplierGeoAnalyticsTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(
            company_name="Geo Supplier",
            email="geo-supplier@example.com",
        )
        self.other = Supplier.objects.create(
            company_name="Other",
            email="other@example.com",
        )
        self.category = Category.objects.create(name_en="Bags", level=1)
        self.product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Bag",
            description="Test",
        )
        self.today = date.today()

    def test_geo_breakdown_uses_visitor_ip_context(self):
        Event.objects.create(
            event_type=Event.EventType.BUYER_PRODUCT_VIEW,
            actor_role=Event.ActorRole.VISITOR,
            context={
                "supplier_id": self.supplier.id,
                "product_id": self.product.id,
                "geo_country": "DZ",
                "geo_country_name": "Algeria",
                "geo_city": "Oran",
            },
        )
        geo = build_supplier_geo_breakdown(
            self.supplier.id,
            self.today.year,
            self.today.month,
        )
        self.assertEqual(geo["locations"][0]["label"], "Oran, Algeria")
        self.assertEqual(geo["locations"][0]["count"], 1)
        self.assertEqual(geo["countries"][0]["label"], "Algeria")

    def test_excludes_supplier_own_events(self):
        from user.models import User

        seller = User.objects.create_user(
            username="geo_seller",
            email="seller@example.com",
            password="test-pass-123",
        )
        seller.supplier = self.supplier
        seller.save(update_fields=["supplier"])
        Event.objects.create(
            event_type=Event.EventType.BUYER_PRODUCT_VIEW,
            actor_role=Event.ActorRole.SELLER,
            actor_user=seller,
            context={
                "supplier_id": self.supplier.id,
                "geo_city": "Should not count",
            },
        )
        geo = build_supplier_geo_breakdown(
            self.supplier.id,
            self.today.year,
            self.today.month,
        )
        self.assertEqual(geo["locations"], [])
