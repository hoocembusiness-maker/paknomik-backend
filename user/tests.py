from django.test import TestCase
from django.urls import reverse

from .models import (
    Category,
    CategoryAttribute,
    Product,
    ProductAttributeValue,
    SearchTag,
    Supplier,
    TechnicalAttribute,
    User,
)
from .product_similarity import ProductSimilarityChecker, find_similar_products


class CoreFlowTests(TestCase):
    def setUp(self):
        self.password = "StrongPass123!"
        self.user = User.objects.create_user(
            username="client1",
            email="client1@example.com",
            password=self.password,
        )
        self.supplier_admin = User.objects.create_user(
            username="supplieradmin",
            email="supplieradmin@example.com",
            password=self.password,
        )
        self.supplier = Supplier.objects.create(
            company_name="Pack Supplier",
            email="supplier@example.com",
        )
        # The supplier/admin link now lives on the user side.
        self.supplier_admin.supplier = self.supplier
        self.supplier_admin.save(update_fields=["supplier"])

    def test_home_page_loads(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_signup_page_loads(self):
        response = self.client.get("/signup/")
        self.assertIn(response.status_code, [200, 301, 302])

    def test_client_dashboard_requires_auth(self):
        response = self.client.get(reverse("client_dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_verified_only_contact_redirects_to_complete_profile(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("contact_supplier", args=[self.supplier.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("complete_profile"), response.url)

    def test_verified_client_can_access_contact_supplier(self):
        self.user.is_verified_client = True
        self.user.save(update_fields=["is_verified_client"])
        # The legacy view hands the conversation over to WhatsApp.
        self.supplier.whatsapp_number = "213555123456"
        self.supplier.save(update_fields=["whatsapp_number"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("contact_supplier", args=[self.supplier.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("wa.me", response.url)

    def test_supplier_list_page_loads(self):
        response = self.client.get(reverse("suppliers"))
        self.assertEqual(response.status_code, 200)


class ProductSimilarityCheckerTests(TestCase):
    def setUp(self):
        self.checker = ProductSimilarityChecker()
        self.parent = Category.objects.create(name_en='Bags', level=1)
        self.subcategory_a = Category.objects.create(
            name_en='Paper Bags',
            level=2,
            parent=self.parent,
        )
        self.subcategory_b = Category.objects.create(
            name_en='Plastic Bags',
            level=2,
            parent=self.parent,
        )
        self.supplier = Supplier.objects.create(
            company_name='Pack Supplier',
            email='supplier@example.com',
        )
        self.material_attr = TechnicalAttribute.objects.create(
            name_en='Material',
            attribute_type='choice',
            attribute_level=1,
        )
        self.width_attr = TechnicalAttribute.objects.create(
            name_en='Width',
            attribute_type='number',
            attribute_level=2,
            is_comparable=True,
        )
        CategoryAttribute.objects.create(
            category=self.subcategory_a,
            attribute=self.material_attr,
        )
        CategoryAttribute.objects.create(
            category=self.subcategory_a,
            attribute=self.width_attr,
        )

    def _create_product(
        self,
        name,
        subcategory,
        material='Kraft',
        width=200.0,
        *,
        description=None,
        custom_keywords=None,
        search_tags=None,
    ):
        product = Product.objects.create(
            supplier=self.supplier,
            category=self.parent,
            subcategory=subcategory,
            name=name,
            description=description if description is not None else name,
            custom_keywords=custom_keywords or [],
        )
        ProductAttributeValue.objects.create(
            product=product,
            attribute=self.material_attr,
            txt_value=material,
        )
        ProductAttributeValue.objects.create(
            product=product,
            attribute=self.width_attr,
            num_value=width,
        )
        if search_tags:
            product.search_tags.set(search_tags)
        return product

    def test_different_subcategories_are_not_comparable(self):
        left = self._create_product('Bag A', self.subcategory_a)
        right = self._create_product('Bag B', self.subcategory_b)

        result = self.checker.compare(left, right)

        self.assertFalse(result.comparable)
        self.assertEqual(result.score, 0.0)

    def test_identical_technical_attributes_score_high(self):
        left = self._create_product('Bag A', self.subcategory_a)
        right = self._create_product('Bag A', self.subcategory_a)

        result = self.checker.compare(left, right)

        self.assertTrue(result.comparable)
        self.assertGreater(result.score, 0.9)
        self.assertEqual(result.shared_attributes, 2)

    def test_numeric_difference_lowers_score(self):
        left = self._create_product('Bag A', self.subcategory_a, width=200.0)
        right = self._create_product('Bag B', self.subcategory_a, width=400.0)

        result = self.checker.compare(left, right)

        self.assertTrue(result.comparable)
        self.assertGreater(result.score, 0.0)
        self.assertLess(result.score, 1.0)

    def test_find_similar_orders_by_score(self):
        source = self._create_product('Source', self.subcategory_a, width=200.0)
        close = self._create_product('Close match', self.subcategory_a, width=205.0)
        far = self._create_product('Far match', self.subcategory_a, width=400.0)

        ranked = self.checker.find_similar(
            source,
            candidates=[far, close],
            min_score=0.0,
        )

        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].product.name, 'Close match')
        self.assertGreater(ranked[0].result.score, ranked[1].result.score)

    def test_find_similar_excludes_other_subcategories(self):
        source = self._create_product('Source', self.subcategory_a, width=200.0)
        same_sub = self._create_product('Same sub', self.subcategory_a, width=210.0)
        other_sub = self._create_product('Other sub', self.subcategory_b, width=210.0)

        ranked = self.checker.find_similar(
            source,
            candidates=[other_sub, same_sub],
            min_score=0.0,
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].product.name, 'Same sub')

    def test_find_similar_products_only_returns_same_subcategory(self):
        source = self._create_product('Source', self.subcategory_a, width=200.0)
        self._create_product('Same sub', self.subcategory_a, width=210.0)
        self._create_product('Other sub', self.subcategory_b, width=210.0)

        similar = find_similar_products(source, limit=5)

        self.assertEqual(len(similar), 1)
        self.assertEqual(similar[0].subcategory_id, source.subcategory_id)

    def test_name_similarity_ranks_closer_product(self):
        source = self._create_product(
            'Sac kraft poignee torsadee',
            self.subcategory_a,
            description='Sac papier kraft avec poignees',
            width=200.0,
        )
        close = self._create_product(
            'Sac kraft poignee plate',
            self.subcategory_a,
            description='Sac papier kraft poignees plates',
            width=500.0,
        )
        far = self._create_product(
            'Boite pizza carton',
            self.subcategory_a,
            description='Emballage pizza en carton ondule',
            width=500.0,
        )

        ranked = self.checker.find_similar(
            source,
            candidates=[far, close],
            min_score=0.0,
        )

        self.assertEqual(ranked[0].product.name, close.name)
        self.assertGreater(ranked[0].result.score, ranked[1].result.score)

    def test_shared_keywords_increase_similarity(self):
        tag_a = SearchTag.objects.create(tag='kraft bag', tag_fr='sac kraft')
        tag_b = SearchTag.objects.create(tag='twisted handle', tag_fr='poignee torsadee')
        left = self._create_product(
            'Bag A',
            self.subcategory_a,
            custom_keywords=['kraft', 'handle'],
            search_tags=[tag_a, tag_b],
        )
        right = self._create_product(
            'Bag B',
            self.subcategory_a,
            custom_keywords=['kraft', 'handle'],
            search_tags=[tag_a],
        )
        unrelated = self._create_product(
            'Bag C',
            self.subcategory_a,
            custom_keywords=['plastic'],
        )

        left_right = self.checker.compare(left, right)
        left_unrelated = self.checker.compare(left, unrelated)

        self.assertGreater(left_right.score, left_unrelated.score)


class BuyerUpgradeSupplierTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.password = 'StrongPass123!'
        self.user = User.objects.create_user(
            username='buyer1',
            email='buyer1@example.com',
            password=self.password,
            first_name='Buyer One',
            company_name='Buyer Co',
        )

    def test_upgrade_creates_supplier_profile(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            '/api/buyer/upgrade-supplier/',
            {
                'companyName': 'Pack Supplier SARL',
                'whatsappNumber': '0555123456',
                'categoryIds': [],
                'subcategoryIds': [],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload['user']['isSupplier'])
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.supplier_id)
        self.assertEqual(self.user.supplier.company_name, 'Pack Supplier SARL')

    def test_already_supplier_cannot_upgrade_again(self):
        supplier = Supplier.objects.create(
            company_name='Existing Supplier',
            email='supplier@example.com',
        )
        self.user.supplier = supplier
        self.user.save(update_fields=['supplier'])
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            '/api/buyer/upgrade-supplier/',
            {'companyName': 'Another Co'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)


class SupplierSelfViewAnalyticsTests(TestCase):
    def setUp(self):
        from datetime import date
        from rest_framework.test import APIClient

        from user.analytics import _load_supplier_period_counts

        self.client = APIClient()
        self.today = date.today()
        self._load_supplier_period_counts = _load_supplier_period_counts
        self.password = "StrongPass123!"
        self.buyer = User.objects.create_user(
            username="buyer1",
            email="buyer1@example.com",
            password=self.password,
        )
        self.supplier_user = User.objects.create_user(
            username="supplier1",
            email="supplier1@example.com",
            password=self.password,
        )
        self.supplier = Supplier.objects.create(
            company_name="Pack Supplier",
            email="supplier@example.com",
        )
        self.supplier_user.supplier = self.supplier
        self.supplier_user.save(update_fields=["supplier"])
        self.category = Category.objects.create(name_en="Bags", level=1)
        self.product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Test Bag",
            description="A test bag",
            is_active=True,
        )

    def test_supplier_viewing_own_profile_does_not_log_event(self):
        from user.models import Event

        self.client.force_authenticate(user=self.supplier_user)
        response = self.client.get(f"/api/suppliers/{self.supplier.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Event.objects.filter(
                event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
                context__supplier_id=self.supplier.pk,
            ).count(),
            0,
        )

    def test_supplier_viewing_own_product_does_not_log_event(self):
        from user.models import Event

        self.client.force_authenticate(user=self.supplier_user)
        response = self.client.get(f"/api/products/{self.product.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Event.objects.filter(
                event_type=Event.EventType.BUYER_PRODUCT_VIEW,
                context__supplier_id=self.supplier.pk,
                context__product_id=self.product.pk,
            ).count(),
            0,
        )

    def test_other_user_profile_view_is_logged(self):
        from user.models import Event

        self.client.force_authenticate(user=self.buyer)
        response = self.client.get(f"/api/suppliers/{self.supplier.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Event.objects.filter(
                event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
                context__supplier_id=self.supplier.pk,
                actor_user=self.buyer,
            ).count(),
            1,
        )

    def test_analytics_excludes_supplier_self_views(self):
        from user.models import Event

        Event.objects.create(
            event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
            actor_role=Event.ActorRole.BUYER,
            actor_user=self.buyer,
            context={"supplier_id": self.supplier.pk},
        )
        Event.objects.create(
            event_type=Event.EventType.BUYER_SUPPLIER_VIEW,
            actor_role=Event.ActorRole.SELLER,
            actor_user=self.supplier_user,
            context={"supplier_id": self.supplier.pk},
        )
        Event.objects.create(
            event_type=Event.EventType.BUYER_PRODUCT_VIEW,
            actor_role=Event.ActorRole.SELLER,
            actor_user=self.supplier_user,
            context={
                "supplier_id": self.supplier.pk,
                "product_id": self.product.pk,
            },
        )

        counts = self._load_supplier_period_counts(
            self.supplier.pk,
            self.today.year,
            self.today.month,
        )
        self.assertEqual(counts["profile_total"], 1)
        self.assertEqual(counts["product_total"], 0)
