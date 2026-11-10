"""Taxonomy schema, stock rules, ranking diversity and alternatives."""

from datetime import date
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook
from rest_framework.test import APIClient

from user import entitlements, rbac
from user.catalog_seed import seed_catalog_taxonomy
from user.catalog_taxonomy import (
    LEVEL_WEIGHT,
    MAX_CARDS_PER_SUPPLIER_PAGE1,
    OFFER_STOCK,
    OFFER_SUR_MESURE,
    SchemaError,
    GammeCard,
    collapse_supplier_types,
    rank_organic,
    stock_publish_error,
    translate_filters,
    validate_attribute_schema,
    validate_attributes,
    variant_summary,
)
from user.models import (
    Category,
    Order,
    Product,
    ProductFamily,
    ProductType,
    TypeAlternative,
)
from user.platform_config import seed_settings
from user.pragmadinar import seed_pragmadinar_catalog
from user.tests_crm_pragmadinar import make_buyer, make_supplier, make_supplier_user

SCHEMA = [
    {
        "key": "capacite",
        "label": "Capacité",
        "kind": "number",
        "unit": "ml",
        "required": True,
        "filterable": True,
        "pivot_key": "capacite",
    },
    {
        "key": "materiau",
        "label": "Matériau",
        "kind": "enum",
        "required": True,
        "filterable": True,
        "pivot_key": "materiau",
        "options": ["PET", "verre"],
    },
    {
        "key": "diametre_col",
        "label": "Diamètre de col",
        "kind": "number",
        "unit": "mm",
        "required": True,
        "filterable": True,
    },
    {
        "key": "couleur",
        "label": "Couleur",
        "kind": "enum",
        "required": False,
        "filterable": True,
        "options": ["transparent", "blanc"],
    },
    {
        "key": "contact_alimentaire",
        "label": "Contact alimentaire",
        "kind": "bool",
        "required": True,
        "filterable": True,
        "pivot_key": "contact_alimentaire",
    },
]


class SchemaTests(TestCase):
    def test_schema_size_and_unknown_value_are_rejected(self):
        with self.assertRaises(SchemaError):
            validate_attribute_schema(SCHEMA[:2])
        cleaned = validate_attribute_schema(SCHEMA)
        self.assertEqual(len(cleaned), 5)
        with self.assertRaises(SchemaError):
            validate_attributes(
                cleaned,
                {"capacite": 100, "inconnu": "x"},
                offer_mode=OFFER_STOCK,
                publishing=False,
            )

    def test_stock_rejects_a_range_and_sur_mesure_accepts_it(self):
        with self.assertRaises(SchemaError):
            validate_attributes(
                SCHEMA,
                {"capacite": {"min": 50, "max": 500}},
                offer_mode=OFFER_STOCK,
                publishing=False,
            )
        cleaned = validate_attributes(
            SCHEMA,
            {"capacite": {"min": 50, "max": 500}, "materiau": ["PET", "verre"]},
            offer_mode=OFFER_SUR_MESURE,
            publishing=False,
        )
        self.assertEqual(cleaned["capacite"]["min"], 50)

    def test_stock_publish_requires_a_priced_variant(self):
        message = stock_publish_error([{"price_from": None, "moq": 10}])
        self.assertIn("sur mesure", message)
        self.assertIsNone(
            stock_publish_error(
                [{"price_from": Decimal("40"), "moq": 100, "is_available": True}]
            )
        )


class RankingTests(TestCase):
    def _card(self, product_id, supplier_id, type_id, score, flagship=False):
        return GammeCard(product_id, supplier_id, type_id, score, flagship)

    def test_one_card_per_supplier_type_prefers_the_flagship(self):
        collapsed = collapse_supplier_types(
            [
                self._card(1, 7, 3, 0.9),
                self._card(2, 7, 3, 0.4, flagship=True),
                self._card(3, 7, 4, 0.8),
            ]
        )
        by_type = {item.card.type_id: item for item in collapsed}
        self.assertEqual(by_type[3].product_id, 2)
        self.assertEqual(by_type[3].other_ids, [1])
        self.assertEqual(len(collapsed), 2)

    def test_page_one_caps_a_supplier_even_when_the_page_is_not_full(self):
        cards = [
            self._card(1, 5, 1, 0.95),
            self._card(2, 5, 2, 0.94),
            self._card(3, 5, 3, 0.93),
        ]
        day = date(2026, 11, 9)
        page1, total = rank_organic(
            cards, page=1, page_size=12, on_date=day, session_slice=0
        )
        page2, _total = rank_organic(
            cards, page=2, page_size=12, on_date=day, session_slice=0
        )
        self.assertEqual(total, 3)
        self.assertEqual(len(page1), MAX_CARDS_PER_SUPPLIER_PAGE1)
        self.assertEqual(len(page2), 1)

    def test_rotation_is_deterministic_and_changes_with_the_session_slice(self):
        cards = [
            self._card(1, 11, 1, 0.90),
            self._card(2, 22, 1, 0.86),
            self._card(3, 33, 1, 0.82),
        ]
        day = date(2026, 11, 9)
        first, _total = rank_organic(
            cards, page=1, page_size=12, on_date=day, session_slice=1
        )
        again, _total = rank_organic(
            cards, page=1, page_size=12, on_date=day, session_slice=1
        )
        self.assertEqual(
            [item.product_id for item in first],
            [item.product_id for item in again],
        )
        orders = {
            tuple(
                item.supplier_id
                for item in rank_organic(
                    cards, page=1, page_size=12, on_date=day, session_slice=slice_id
                )[0]
            )
            for slice_id in range(8)
        }
        self.assertGreater(len(orders), 1)

    def test_required_filter_without_a_translation_drops_the_alternative(self):
        target = []
        for item in SCHEMA:
            if item["key"] == "diametre_col":
                continue
            if item["key"] == "capacite":
                target.append({**item, "key": "volume"})
            else:
                target.append(item)
        blocked = translate_filters(
            {"diametre_col": 24, "capacite": 100},
            {"capacite": "volume"},
            SCHEMA,
            target,
        )
        self.assertTrue(blocked.blocked)
        mapped = translate_filters(
            {"capacite": 100}, {"capacite": "volume"}, SCHEMA, target
        )
        self.assertFalse(mapped.blocked)
        self.assertEqual(mapped.filters, {"volume": 100})

    def test_variant_summary_counts_axes_once_per_gamme(self):
        text = variant_summary(
            [
                {
                    "axes": {"capacite": 50, "couleur": "blanc"},
                    "price_from": 40,
                    "is_available": True,
                },
                {
                    "axes": {"capacite": 100, "couleur": "blanc"},
                    "price_from": 55,
                    "is_available": True,
                },
            ],
            SCHEMA,
        )
        self.assertIn("2 capacités", text)
        self.assertIn("1 couleur", text)
        self.assertIn("dès 40 DA", text)


class CatalogApiTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        seed_pragmadinar_catalog()
        seed_settings()
        rbac.seed_roles()
        self.family_category = Category.objects.create(
            name_en="Containers", name_fr="Contenants", slug="contenants-api", level=1
        )
        self.type_category = Category.objects.create(
            name_en="Bottle",
            name_fr="Flacon",
            slug="flacon-api",
            parent=self.family_category,
            level=2,
        )
        self.family = ProductFamily.objects.create(
            slug="contenants-api", name_fr="Contenants"
        )
        self.product_type = ProductType.objects.create(
            family=self.family,
            slug="flacon-api",
            name_fr="Flacon",
            attribute_schema=SCHEMA,
            legacy_category=self.type_category,
        )
        self.pot_category = Category.objects.create(
            name_en="Jar",
            name_fr="Pot",
            slug="pot-api",
            parent=self.family_category,
            level=2,
        )
        self.pot_type = ProductType.objects.create(
            family=self.family,
            slug="pot-api",
            name_fr="Pot",
            attribute_schema=SCHEMA,
            legacy_category=self.pot_category,
        )
        TypeAlternative.objects.create(
            from_type=self.product_type,
            to_type=self.pot_type,
            level="PROCHE",
            bidirectional=True,
            attribute_map={"capacite": "capacite", "materiau": "materiau"},
            reason="même usage : contenir un liquide",
        )
        self.supplier = make_supplier("Taxo Co", plan="genereux")
        self.supplier.kind = "DISTRIBUTOR"
        self.supplier.save(update_fields=["kind"])
        user = make_supplier_user(self.supplier, email="taxo@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user)
        self.buyer = make_buyer("taxo")
        self.buyer.wilaya = "algiers"
        self.buyer.save(update_fields=["wilaya"])
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)

    def _payload(self, **extra):
        body = {
            "productTypeId": self.product_type.pk,
            "name": "Flacon rond",
            "description": "Gamme de flacons ronds pour liquide alimentaire.",
            "offerMode": OFFER_STOCK,
            "attributes": {
                "capacite": 100,
                "materiau": "PET",
                "diametre_col": 24,
                "contact_alimentaire": True,
            },
            "variants": [
                {"axes": {"couleur": "blanc"}, "priceFrom": "40.00", "moq": 100},
                {"axes": {"couleur": "transparent"}, "priceFrom": "42.00", "moq": 100},
            ],
            "publish": True,
        }
        body.update(extra)
        return body

    def test_stock_without_price_is_refused_and_suggests_custom(self):
        response = self.client.post(
            reverse("api_catalog_products"),
            self._payload(variants=[{"axes": {"couleur": "blanc"}}]),
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["suggestOfferMode"], OFFER_SUR_MESURE)

    def test_published_gamme_is_one_card_and_can_be_ordered_by_variant(self):
        created = self.client.post(
            reverse("api_catalog_products"), self._payload(), format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        product = Product.objects.get(pk=created.data["id"])
        self.assertEqual(product.category_id, self.family_category.pk)
        self.assertEqual(product.listing_type, Product.ListingType.SELL)
        self.assertEqual(product.variants.count(), 2)

        found = self.client.get(
            reverse("api_catalog_search"), {"typeId": self.product_type.pk}
        )
        self.assertEqual(found.status_code, 200)
        self.assertEqual(len(found.data["results"]), 1)
        self.assertEqual(found.data["results"][0]["badge"], "Disponible")
        self.assertIn("2 couleurs", found.data["results"][0]["summary"])
        self.assertEqual(found.data["results"][0]["primaryAction"], "order")

        variant = product.variants.order_by("price_from").first()
        ordered = self.buyer_client.post(
            reverse("api_orders"),
            {
                "productId": product.pk,
                "variantId": variant.pk,
                "quantity": variant.moq,
                "wilaya": "algiers",
            },
            format="json",
        )
        self.assertEqual(ordered.status_code, 201, ordered.data)
        order = Order.objects.get(product=product)
        self.assertEqual(order.variant_id, variant.pk)
        self.assertEqual(order.unit_price, variant.price_from)

    def test_custom_product_cannot_be_ordered_and_empty_search_promotes_alternatives(self):
        created = self.client.post(
            reverse("api_catalog_products"),
            self._payload(
                productTypeId=self.pot_type.pk,
                offerMode=OFFER_SUR_MESURE,
                name="Pot sur mesure",
                attributes={
                    "capacite": {"min": 50, "max": 500},
                    "materiau": ["PET", "verre"],
                    "diametre_col": {"min": 18, "max": 28},
                    "contact_alimentaire": True,
                },
                variants=[],
            ),
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        pot = Product.objects.get(name="Pot sur mesure")
        refused = self.buyer_client.post(
            reverse("api_orders"),
            {"productId": pot.pk, "quantity": 10, "wilaya": "algiers"},
            format="json",
        )
        self.assertEqual(refused.status_code, 400)

        found = self.client.get(
            reverse("api_catalog_search"), {"typeId": self.product_type.pk}
        )
        self.assertEqual(found.data["results"], [])
        self.assertTrue(found.data["alternativesPromoted"])
        self.assertEqual(len(found.data["alternatives"]), 1)
        self.assertFalse(found.data["alternativesBillable"])
        self.assertEqual(found.data["alternatives"][0]["badge"], "Sur mesure")
        self.assertEqual(found.data["alternatives"][0]["primaryAction"], "quote")
        self.assertIn("même usage", found.data["alternatives"][0]["reason"])
        self.assertLessEqual(found.data["alternatives"][0]["score"], LEVEL_WEIGHT["PROCHE"])

    def test_required_filter_without_equivalent_hides_the_alternative(self):
        self.client.post(
            reverse("api_catalog_products"),
            self._payload(productTypeId=self.pot_type.pk, name="Pot stock"),
            format="json",
        )
        found = self.client.get(
            reverse("api_catalog_search"),
            {"typeId": self.product_type.pk, "filters": '{"diametre_col": 24}'},
        )
        self.assertEqual(found.data["alternatives"], [])

    def test_historical_product_list_stays_a_flat_list(self):
        Product.objects.create(
            supplier=self.supplier,
            category=self.family_category,
            name="Ancien carton",
            description="Ancien",
            price=Decimal("10"),
        )
        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)

    def test_excel_import_creates_variants(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "Gammes"
        sheet.append(
            [
                "nom",
                "description",
                "mode",
                "capacite",
                "materiau",
                "diametre_col",
                "contact_alimentaire",
            ]
        )
        sheet.append(
            [
                "Importé",
                "Gamme importée pour le test automatique.",
                "STOCK",
                250,
                "PET",
                28,
                "oui",
            ]
        )
        variants = book.create_sheet("Variantes")
        variants.append(
            [
                "nom_gamme",
                "sku",
                "axe_1",
                "valeur_1",
                "axe_2",
                "valeur_2",
                "axe_3",
                "valeur_3",
                "prix_depart",
                "moq",
                "disponible",
            ]
        )
        variants.append(
            ["Importé", "IMP-1", "couleur", "blanc", "", "", "", "", 30, 50, "oui"]
        )
        buffer = BytesIO()
        book.save(buffer)
        buffer.seek(0)
        response = self.client.post(
            reverse("api_catalog_import", args=[self.product_type.pk]),
            {"file": buffer},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(name="Importé")
        self.assertEqual(product.variants.count(), 1)
        self.assertEqual(product.variants.get().axes["couleur"], "blanc")

    def test_seed_is_idempotent_and_keeps_old_categories(self):
        before = Category.objects.count()
        first = seed_catalog_taxonomy()
        second = seed_catalog_taxonomy()
        self.assertEqual(first, second)
        self.assertGreaterEqual(Category.objects.count(), before)
        self.assertTrue(ProductType.objects.filter(slug="flacon").exists())
        self.assertTrue(ProductType.objects.filter(slug="boite-compartimentee").exists())
        self.assertTrue(
            TypeAlternative.objects.filter(
                from_type__slug="flacon", to_type__slug="pot"
            ).exists()
        )


class QuotePrefillTests(TestCase):
    def test_prefill_lists_alternatives_without_sending_them(self):
        family = ProductFamily.objects.create(slug="fam-prefill", name_fr="Contenants")
        source = ProductType.objects.create(
            family=family, slug="src-prefill", name_fr="Flacon", attribute_schema=SCHEMA
        )
        target = ProductType.objects.create(
            family=family, slug="dst-prefill", name_fr="Pot", attribute_schema=SCHEMA
        )
        TypeAlternative.objects.create(
            from_type=source,
            to_type=target,
            level="PROCHE",
            reason="même usage : contenir un liquide",
            attribute_map={},
        )
        response = APIClient().get(
            reverse("api_catalog_quote_prefill", args=[source.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["autoSend"])
        self.assertEqual(response.data["alternatives"][0]["label"], "Pot")
        self.assertTrue(response.data["technicalCharacteristics"])

