import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from user.models import (
    BuyerFavoriteSupplier,
    Category,
    Event,
    Order,
    Product,
    RFQRequest,
    Supplier,
    User,
)
from user.platform_config import set_setting


def _stamp(event):
    Event.objects.filter(pk=event.pk).update(created_at=timezone.now())
    return event


def _appearance(supplier_id, source):
    return _stamp(
        Event.objects.create(
            event_type=Event.EventType.SYSTEM_APPEAR_IN_SEARCH,
            actor_role=Event.ActorRole.SYSTEM,
            context={"supplier_id": supplier_id, "source": source, "search_id": f"{supplier_id}-{source}-{Event.objects.count()}"},
        )
    )


class CategoryBenchmarkTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.parent = Category.objects.create(name_en="Pack", name_fr="Emballage", slug="pack-bench")
        self.sub_a = Category.objects.create(
            name_en="Bottles", name_fr="Bouteilles", slug="bottles-bench", parent=self.parent, level=2
        )
        self.sub_b = Category.objects.create(
            name_en="Jars", name_fr="Pots", slug="jars-bench", parent=self.parent, level=2
        )
        self.supplier = Supplier.objects.create(company_name="ARTPAP", email="artpap-bench@example.com")
        self.seller = User.objects.create_user(
            username="artpap_bench", email="artpap-bench-user@example.com", password="x-pass-123"
        )
        self.seller.supplier = self.supplier
        self.seller.save(update_fields=["supplier"])
        self.buyer = User.objects.create_user(
            username="bench_buyer", email="bench-buyer@example.com", password="x-pass-123"
        )
        Product.objects.create(
            supplier=self.supplier,
            category=self.parent,
            subcategory=self.sub_a,
            name="Bouteille",
            description="b",
        )
        set_setting("benchmark_min_suppliers", "1")

    def _payload(self, **params):
        self.client.force_authenticate(self.seller)
        response = self.client.get("/api/supplier/market/category-benchmark/", {"period": "1d", **params})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _card(self, payload, key):
        return next(item for item in payload["visibility"] if item["key"] == key)

    def _activity(self, payload, key):
        return next(item for item in payload["activity"] if item["key"] == key)

    def _add_peer(self, name, appearances, subcategory):
        peer = Supplier.objects.create(company_name=name, email=f"{name}@example.com")
        Product.objects.create(
            supplier=peer, category=self.parent, subcategory=subcategory, name=name, description="d"
        )
        for _ in range(appearances):
            _appearance(peer.id, "products")
        return peer

    def test_supplier_totals_stay_within_the_category_and_shares_are_bounded(self):
        for _ in range(2):
            _appearance(self.supplier.id, "products")
        _appearance(self.supplier.id, "suppliers")
        self._add_peer("PeerOne", 1, self.sub_a)
        Order.objects.create(
            supplier=self.supplier,
            product=self.supplier.products.first(),
            quantity=1,
            unit_price="10.00",
        )
        RFQRequest.objects.create(supplier=self.supplier, name="Devis", company="A")
        BuyerFavoriteSupplier.objects.create(buyer=self.buyer, supplier=self.supplier)

        payload = self._payload()
        visibility = {item["key"]: item for item in payload["visibility"]}

        def volume(key):
            value = int(visibility[key]["value"])
            pill = visibility[key]["pill"]
            own = int(pill.split(" (", 1)[0])
            share = int(pill.split("(", 1)[1].rstrip("%)"))
            return value, own, share

        total, total_own, total_share = volume("search_appearances")
        store, store_own, _store_share = volume("store_appearances")
        product, product_own, _product_share = volume("product_search_appearances")
        self.assertEqual(total, store + product)
        self.assertEqual(total_own, store_own + product_own)
        self.assertLessEqual(total_own, total)
        self.assertLessEqual(store_own, store)
        self.assertLessEqual(product_own, product)
        self.assertGreaterEqual(total_share, 0)
        self.assertLessEqual(total_share, 100)

        def own_count(card):
            pill = card["pill"]
            if pill in (None, "—"):
                return 0
            return int(pill.split(" (", 1)[0])

        interactions = volume("interactions")
        orders = own_count(self._activity(payload, "orders"))
        quotes = own_count(self._activity(payload, "quotes"))
        samples = own_count(self._activity(payload, "samples"))
        conversations = own_count(self._activity(payload, "conversations"))
        favorites = own_count(self._activity(payload, "product_favorites"))
        self.assertEqual(interactions[1], orders + quotes + samples + conversations + favorites)
        self.assertEqual(
            int(visibility["interactions"]["value"]),
            int(self._activity(payload, "orders")["value"])
            + int(self._activity(payload, "quotes")["value"])
            + int(self._activity(payload, "samples")["value"])
            + int(self._activity(payload, "conversations")["value"])
            + int(self._activity(payload, "product_favorites")["value"]),
        )
        _stamp(
            Event.objects.create(
                event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
                actor_role=Event.ActorRole.BUYER,
                context={"supplier_id": self.supplier.id},
            )
        )
        _stamp(
            Event.objects.create(
                event_type=Event.EventType.BUYER_PRODUCT_VIEW,
                actor_role=Event.ActorRole.BUYER,
                context={"supplier_id": self.supplier.id, "product_id": self.supplier.products.first().id},
            )
        )
        payload = self._payload()
        visibility = {item["key"]: item for item in payload["visibility"]}

        def volume(key):
            value = int(visibility[key]["value"])
            pill = visibility[key]["pill"]
            own = int(pill.split(" (", 1)[0])
            share = int(pill.split("(", 1)[1].rstrip("%)"))
            return value, own, share

        visits, visits_own, _visits_share = volume("all_visits")
        store_visits, store_visits_own, _ = volume("store_visits")
        product_visits, product_visits_own, _ = volume("product_visits")
        self.assertEqual(visits, store_visits + product_visits)
        self.assertEqual(visits_own, store_visits_own + product_visits_own)
        self.assertEqual((visits, visits_own), (2, 2))
        self.assertEqual(self._activity(payload, "followers")["pill"].split(" (", 1)[0], "1")
        self.assertEqual(visibility["appearance_rate"]["label"], "Pourcentage d'apparition de la catégorie")
        self.assertEqual(
            visibility["product_visit_share"]["label"],
            "Proportion de visites de pages produit par rapport au marché",
        )
        self.assertEqual(
            visibility["product_appearance_share"]["label"],
            "Pourcentage d'apparition de produits par rapport au marché",
        )
        self.assertIn("categoryDetailed", payload["chart"]["series"][0])

    def test_zero_division_renders_a_dash(self):
        payload = self._payload()
        rate = self._card(payload, "appearance_rate")
        self.assertEqual(rate["value"], "—")
        self.assertEqual(rate["pill"], "—")
        appearances = self._card(payload, "search_appearances")
        self.assertEqual(appearances["value"], "0")
        self.assertEqual(appearances["pill"], "—")
        self.assertNotIn("NaN", json.dumps(payload))

    def test_all_subcategories_count_each_supplier_and_event_once(self):
        Product.objects.create(
            supplier=self.supplier,
            category=self.parent,
            subcategory=self.sub_b,
            name="Pot",
            description="p",
        )
        _appearance(self.supplier.id, "products")
        peer = self._add_peer("PeerShared", 1, self.sub_a)
        payload = self._payload()
        self.assertEqual(payload["group"]["supplierCount"], 2)
        self.assertEqual(self._card(payload, "search_appearances")["value"], "2")
        self.assertNotEqual(peer.company_name, payload["companyName"])
        self.assertNotIn(peer.company_name, json.dumps(payload))

    def test_small_group_hides_category_values_and_other_suppliers(self):
        set_setting("benchmark_min_suppliers", "5")
        peer = self._add_peer("ZZRivalUnique", 87, self.sub_a)
        _appearance(self.supplier.id, "products")
        self.client.force_authenticate(self.seller)
        response = self.client.get("/api/supplier/market/category-benchmark/", {"period": "1d"})
        payload = response.json()
        body = json.dumps(payload)
        self.assertTrue(payload["group"]["masked"])
        self.assertNotIn("ZZRivalUnique", body)
        self.assertNotIn("87", body)
        self.assertNotIn("88", body)
        for card in payload["visibility"]:
            self.assertEqual(card["value"], "—")
            self.assertEqual(card["pill"], "—")
            self.assertNotIn("categoryValue", card)
        for series in payload["chart"]["series"]:
            self.assertNotIn("categoryDetailed", series)
            self.assertNotIn("categoryCumulative", series)
        self.assertIsNone(payload["chart"]["categoryLabel"])
        self.assertEqual(payload["group"]["hint"], "Données de catégorie insuffisantes pour préserver l'anonymat")
