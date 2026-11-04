"""
Automated tests for CRM, Pragmadinar wallet, consent, and plan limits.

Run with: python manage.py test user.tests_crm_pragmadinar
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from user import crm, entitlements, rbac
from user.models import (
    AuditLog,
    Category,
    ConsentRecord,
    CrmCompany,
    Opportunity,
    PragmadinarLedger,
    PragmadinarService,
    PlanEntitlement,
    Order,
    Product,
    QuoteDemand,
    QuoteDemandSelection,
    QuoteResponse,
    RFQRequest,
    Supplier,
)
from user.pragmadinar import (
    InsufficientPragmadinar,
    credit_wallet,
    debit_service,
    get_or_create_wallet,
    get_service,
    hold_service,
    refund_entry,
    release_hold,
    settle_hold,
)
from user.platform_config import pragmadinar_rate_dzd, pragmadinar_to_dzd, set_setting

User = get_user_model()


OPPORTUNITY_CODE = "commercial.category_leads"
PAK_DATING_CODE = "commercial.pak_dating"


def make_supplier(name, *, plan="business", email=None):
    return Supplier.objects.create(
        company_name=name,
        email=email or f"{name.lower().replace(' ', '')}@example.com",
        wilaya="algiers",
        paknomik_plan=plan,
    )


def make_supplier_user(supplier, *, email=None):
    return User.objects.create_user(
        username=f"user-{supplier.pk}",
        email=email or f"user{supplier.pk}@example.com",
        password="Testpass!2345",
        supplier=supplier,
    )


def make_buyer(suffix="1"):
    return User.objects.create_user(
        username=f"buyer{suffix}",
        email=f"buyer{suffix}@example.com",
        password="Testpass!2345",
        company_name=f"Acheteur {suffix}",
        wilaya="oran",
        phone_number="+213555000000",
    )


class EntitlementTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        self.free = make_supplier("Free Co", plan="genereux")
        self.paid = make_supplier("Paid Co", plan="business")
        credit_wallet(self.paid, 10)
        self.category = Category.objects.create(name_en="Boxes", level=1)

    def _add_products(self, supplier, count):
        for index in range(count):
            Product.objects.create(
                supplier=supplier,
                name=f"P{index}",
                category=self.category,
                subcategory=self.category,
            )

    def test_genereux_is_capped_at_three_products(self):
        self._add_products(self.free, 3)
        with self.assertRaises(entitlements.QuotaExceeded):
            entitlements.assert_can_add_product(self.free)

    def test_genereux_allows_the_third_product(self):
        self._add_products(self.free, 2)
        self.assertTrue(entitlements.assert_can_add_product(self.free))

    def test_business_catalogue_is_unlimited(self):
        self._add_products(self.paid, 25)
        self.assertTrue(entitlements.assert_can_add_product(self.paid))

    def test_monthly_quota_is_counted_and_then_blocks(self):
        key = entitlements.PRODUCT_IMPRESSIONS_MONTHLY
        for _ in range(50):
            entitlements.consume(self.free, key)
        snapshot = entitlements.quota_snapshot(self.free, key)
        self.assertEqual(snapshot["used"], 50)
        self.assertEqual(snapshot["limit"], 50)
        self.assertEqual(snapshot["percentUsed"], 100)
        self.assertTrue(snapshot["exhausted"])
        with self.assertRaises(entitlements.QuotaExceeded):
            entitlements.consume(self.free, key)

    def test_quota_snapshot_warns_before_the_wall(self):
        key = entitlements.PRODUCT_VISITS_MONTHLY
        for _ in range(45):
            entitlements.consume(self.free, key)
        snapshot = entitlements.quota_snapshot(self.free, key)
        self.assertEqual(snapshot["remaining"], 5)
        self.assertEqual(snapshot["percentUsed"], 90)
        self.assertFalse(snapshot["exhausted"])

    def test_exhausted_quota_never_deletes_data(self):
        self._add_products(self.free, 3)
        with self.assertRaises(entitlements.QuotaExceeded):
            entitlements.assert_can_add_product(self.free)
        self.assertEqual(self.free.products.count(), 3)

    def test_business_quotas_are_unlimited(self):
        key = entitlements.PRODUCT_IMPRESSIONS_MONTHLY
        for _ in range(200):
            entitlements.consume(self.paid, key)
        snapshot = entitlements.quota_snapshot(self.paid, key)
        self.assertTrue(snapshot["unlimited"])
        self.assertIsNone(snapshot["limit"])

    def test_always_track_keeps_counting_past_the_plan_limit(self):
        key = entitlements.PRODUCT_IMPRESSIONS_MONTHLY
        for _ in range(50):
            entitlements.consume(self.free, key, always_track=True)
        entitlements.consume(self.free, key, enforce=False, always_track=True)
        snapshot = entitlements.quota_snapshot(self.free, key)
        self.assertEqual(snapshot["used"], 51)
        self.assertTrue(snapshot["exhausted"])

    def test_limit_is_changed_from_configuration_not_code(self):
        PlanEntitlement.objects.filter(
            plan_code="genereux", key=entitlements.PRODUCTS_MAX
        ).update(limit_value=5)
        self._add_products(self.free, 4)
        self.assertTrue(entitlements.assert_can_add_product(self.free))

    def test_locked_feature_raises_with_upgrade_path(self):
        PlanEntitlement.objects.filter(
            plan_code="genereux", key=entitlements.FEATURE_BENCHMARKING
        ).update(limit_value=0)
        with self.assertRaises(entitlements.FeatureLocked) as ctx:
            entitlements.require_feature(self.free, entitlements.FEATURE_BENCHMARKING)
        self.assertEqual(ctx.exception.detail["code"], "feature_locked")
        self.assertEqual(ctx.exception.detail["upgradePath"], "/supplier/wallet")

    def test_crm_is_free_for_all_suppliers(self):
        self.assertTrue(entitlements.has_feature(self.free, entitlements.FEATURE_CRM))
        self.assertTrue(entitlements.require_feature(self.free, entitlements.FEATURE_CRM))
        self.assertTrue(entitlements.require_feature(self.paid, entitlements.FEATURE_CRM))

    def test_business_is_active_while_pragmadinar_remain(self):
        credit_wallet(self.free, 1)
        self._add_products(self.free, 3)
        self.assertEqual(entitlements.plan_code_for(self.free), "business")
        self.assertTrue(entitlements.assert_can_add_product(self.free))
        payload = entitlements.entitlements_payload(self.free)
        self.assertEqual(payload["plan"], "business")
        self.assertIsNone(payload["planUntil"])
        self.assertEqual(payload["pragmadinarAvailable"], "1.00")

    def test_exhausted_pragmadinar_fall_back_to_genereux(self):
        self._add_products(self.paid, 3)
        wallet = get_or_create_wallet(self.paid)
        wallet.balance = Decimal("0")
        wallet.save(update_fields=["balance"])
        self.assertEqual(entitlements.plan_code_for(self.paid), "genereux")
        with self.assertRaises(entitlements.QuotaExceeded) as ctx:
            entitlements.assert_can_add_product(self.paid)
        self.assertEqual(ctx.exception.detail["upgradePath"], "/supplier/wallet")

    def test_held_pragmadinar_do_not_keep_business_active(self):
        wallet = get_or_create_wallet(self.paid)
        wallet.held_total = wallet.balance
        wallet.save(update_fields=["held_total"])
        self.assertEqual(entitlements.plan_code_for(self.paid), "genereux")

    def test_stored_plan_field_no_longer_grants_business(self):
        self.assertEqual(self.free.paknomik_plan, "genereux")
        empty = make_supplier("Empty Co", plan="business")
        self.assertEqual(entitlements.plan_code_for(empty), "genereux")


class PragmadinarWalletTests(TestCase):
    def setUp(self):
        from user.pragmadinar import seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        entitlements.seed_entitlements()
        self.supplier = make_supplier("Wallet Co")
        self.lead_cost = get_service(OPPORTUNITY_CODE).cost_pragmadinar
        self.dating_cost = get_service(PAK_DATING_CODE).cost_pragmadinar

    def _credit(self, amount, **kwargs):
        """Both catalog services used here are billed from the promotion wallet."""
        return credit_wallet(self.supplier, amount, wallet_category="promotion", **kwargs)

    def _wallet(self):
        return get_or_create_wallet(self.supplier, category="promotion")

    def _total(self, field):
        from user.models import PragmadinarWallet

        return sum(
            (getattr(w, field) for w in PragmadinarWallet.objects.filter(supplier=self.supplier)),
            Decimal("0"),
        )

    def test_one_pragmadinar_is_two_hundred_dinars(self):
        self.assertEqual(pragmadinar_rate_dzd(), Decimal("200"))
        self.assertEqual(pragmadinar_to_dzd(10), Decimal("2000.00"))

    def test_welcome_grant_is_worth_twenty_thousand_dinars(self):
        from user.pragmadinar import grant_supplier_welcome_pragmadinar

        entries = grant_supplier_welcome_pragmadinar(self.supplier)
        self.assertEqual({entry.wallet.category for entry in entries}, {"visibility"})
        self.assertEqual(sum(entry.amount for entry in entries), Decimal("200"))
        self.assertEqual(sum(entry.amount_dzd for entry in entries), Decimal("40000.00"))

    def test_format_pragmadinar_amount_uses_cent_precision(self):
        from user.pragmadinar import format_pragmadinar_amount, wallet_payload

        self.assertEqual(format_pragmadinar_amount(Decimal("10")), "10.00")
        self.assertEqual(format_pragmadinar_amount(Decimal("0.1")), "0.10")
        credit_wallet(self.supplier, Decimal("0.01"))
        payload = wallet_payload(self.supplier)
        self.assertEqual(payload["available"], "0.01")

    def test_supplier_welcome_pragmadinar_defaults_to_100(self):
        from user.platform_config import supplier_welcome_pragmadinar

        self.assertEqual(supplier_welcome_pragmadinar(), Decimal("200"))

    def test_welcome_grant_is_idempotent(self):
        from user.pragmadinar import grant_supplier_welcome_pragmadinar

        first = grant_supplier_welcome_pragmadinar(self.supplier)
        second = grant_supplier_welcome_pragmadinar(self.supplier)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(self._total("balance"), Decimal("200"))
        self.assertEqual(self._total("granted_total"), Decimal("200"))

    def test_legacy_grant500_normalized_to_welcome_amount(self):
        from user.models import PragmadinarLedger
        from user.pragmadinar import (
            LEGACY_WELCOME_GRANT_PREFIX,
            credit_wallet,
            normalize_legacy_welcome_grant,
        )

        credit_wallet(
            self.supplier,
            Decimal("500"),
            reason="Crédit de bienvenue Pragmadinar",
            entry_type=PragmadinarLedger.EntryType.GRANT,
            idempotency_key=f"{LEGACY_WELCOME_GRANT_PREFIX}{self.supplier.pk}",
            wallet_category="legacy",
        )
        wallet = get_or_create_wallet(self.supplier, category="legacy")
        self.assertEqual(wallet.balance, Decimal("500"))

        normalize_legacy_welcome_grant(self.supplier)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("200"))
        self.assertEqual(wallet.granted_total, Decimal("200"))
        entry = PragmadinarLedger.objects.get(
            wallet=wallet,
            idempotency_key=f"bootstrap:grant200:v1:{self.supplier.pk}",
        )
        self.assertEqual(entry.amount, Decimal("200"))

    def test_rate_is_configurable(self):
        set_setting("pragmadinar_rate_dzd", "150")
        self.assertEqual(pragmadinar_to_dzd(2), Decimal("300.00"))

    def test_debit_refuses_when_balance_is_insufficient(self):
        with self.assertRaises(InsufficientPragmadinar):
            debit_service(self.supplier, OPPORTUNITY_CODE, reason="test")
        self.assertEqual(self._wallet().balance, Decimal("0"))

    def test_balance_never_goes_negative(self):
        partial = self.lead_cost / 2
        self._credit(partial)
        with self.assertRaises(InsufficientPragmadinar):
            debit_service(self.supplier, OPPORTUNITY_CODE, reason="test")
        self.assertEqual(self._wallet().balance, partial)

    def test_debit_can_draw_on_the_supplier_total(self):
        credit_wallet(self.supplier, 50, wallet_category="visibility")
        debit_service(self.supplier, OPPORTUNITY_CODE)
        self.assertEqual(self._total("balance"), Decimal("50") - self.lead_cost)

    def test_debit_records_amount_reference_and_dinar_equivalent(self):
        self._credit(50)
        entry = debit_service(self.supplier, OPPORTUNITY_CODE, reason="Opportunité")
        self.assertEqual(entry.amount, -self.lead_cost)
        self.assertEqual(entry.amount_dzd, pragmadinar_to_dzd(self.lead_cost))
        self.assertTrue(entry.reference)
        self.assertEqual(entry.entry_type, PragmadinarLedger.EntryType.SPEND)
        self.assertEqual(self._wallet().balance, 50 - self.lead_cost)

    def test_repeated_operation_does_not_double_debit(self):
        self._credit(50)
        first = debit_service(
            self.supplier, OPPORTUNITY_CODE, idempotency_key="demand:7"
        )
        second = debit_service(
            self.supplier, OPPORTUNITY_CODE, idempotency_key="demand:7"
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self._wallet().balance, 50 - self.lead_cost)

    def test_every_transaction_is_visible_in_history(self):
        self._credit(30, reason="Recharge sandbox")
        debit_service(self.supplier, OPPORTUNITY_CODE, reason="Opportunité")
        entries = PragmadinarLedger.objects.filter(wallet__supplier=self.supplier)
        self.assertEqual(entries.count(), 2)
        for entry in entries:
            self.assertTrue(entry.reason)
            self.assertTrue(entry.reference)

    def test_wallet_totals_track_credits_and_spending(self):
        self._credit(20, entry_type=PragmadinarLedger.EntryType.GRANT)
        self._credit(10, entry_type=PragmadinarLedger.EntryType.PURCHASE)
        debit_service(self.supplier, OPPORTUNITY_CODE)
        wallet = self._wallet()
        self.assertEqual(wallet.granted_total, Decimal("20"))
        self.assertEqual(wallet.purchased_total, Decimal("10"))
        self.assertEqual(wallet.spent_total, self.lead_cost)
        self.assertEqual(wallet.balance, 30 - self.lead_cost)

    def test_hold_reserves_without_spending_then_settles(self):
        self._credit(30)
        hold = hold_service(self.supplier, PAK_DATING_CODE)
        wallet = self._wallet()
        self.assertEqual(wallet.balance, Decimal("30"))
        self.assertEqual(wallet.held_total, self.dating_cost)
        self.assertEqual(wallet.available_balance, 30 - self.dating_cost)

        settle_hold(hold)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 30 - self.dating_cost)
        self.assertEqual(wallet.held_total, Decimal("0"))

    def test_released_hold_gives_the_reservation_back(self):
        self._credit(30)
        hold = hold_service(self.supplier, PAK_DATING_CODE)
        release_hold(hold)
        wallet = self._wallet()
        self.assertEqual(wallet.balance, Decimal("30"))
        self.assertEqual(wallet.held_total, Decimal("0"))
        self.assertEqual(
            PragmadinarLedger.objects.get(pk=hold.pk).status,
            PragmadinarLedger.Status.RELEASED,
        )

    def test_held_amount_cannot_be_spent_twice(self):
        self._credit(self.dating_cost)
        hold_service(self.supplier, PAK_DATING_CODE)
        with self.assertRaises(InsufficientPragmadinar):
            debit_service(self.supplier, OPPORTUNITY_CODE)

    def test_technical_refund_restores_the_balance_once(self):
        self._credit(20)
        entry = debit_service(self.supplier, OPPORTUNITY_CODE)
        refund_entry(entry, reason="Incident technique")
        self.assertEqual(self._wallet().balance, Decimal("20"))
        entry.refresh_from_db()
        with self.assertRaises(Exception):
            refund_entry(entry, reason="Doublon")

    def test_price_change_applies_without_touching_the_code(self):
        self._credit(50)
        PragmadinarService.objects.filter(code=OPPORTUNITY_CODE).update(
            cost_pragmadinar=Decimal("25")
        )
        entry = debit_service(self.supplier, OPPORTUNITY_CODE)
        self.assertEqual(entry.amount, Decimal("-25"))


class RbacTests(TestCase):
    def setUp(self):
        rbac.seed_roles()
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="Testpass!2345"
        )
        self.finance = User.objects.create_user(
            username="finance", email="finance@example.com", password="Testpass!2345"
        )
        self.plain = User.objects.create_user(
            username="plain", email="plain@example.com", password="Testpass!2345"
        )

    def _make_ultra(self, user):
        from user.models import PlatformRole, RoleGrant

        RoleGrant.objects.create(
            user=user, role=PlatformRole.objects.get(code=rbac.ULTRA_ADMIN)
        )

    def test_a_plain_user_has_no_permission(self):
        self.assertEqual(rbac.user_permissions(self.plain), set())
        with self.assertRaises(PermissionDenied):
            rbac.require_permission(self.plain, rbac.AUDIT_READ)

    def test_ultra_admin_holds_every_permission(self):
        self._make_ultra(self.owner)
        self.assertTrue(rbac.is_ultra_admin(self.owner))
        for permission in rbac.ALL_PERMISSIONS:
            self.assertTrue(rbac.has_permission(self.owner, permission))

    def test_a_user_cannot_grant_themselves_ultra_admin(self):
        with self.assertRaises(PermissionDenied):
            rbac.grant_role(self.plain, rbac.ULTRA_ADMIN, granted_by=self.plain)
        self.assertFalse(rbac.is_ultra_admin(self.plain))

    def test_a_regular_admin_cannot_create_an_ultra_admin(self):
        self._grant_system(self.finance, rbac.ADMIN_GENERAL)
        with self.assertRaises(PermissionDenied):
            rbac.grant_role(self.plain, rbac.ULTRA_ADMIN, granted_by=self.finance)

    def _grant_system(self, user, role_code):
        from user.models import PlatformRole, RoleGrant

        RoleGrant.objects.create(
            user=user, role=PlatformRole.objects.get(code=role_code)
        )

    def test_finance_can_refund_but_not_manage_roles(self):
        self._grant_system(self.finance, rbac.ADMIN_FINANCE)
        self.assertTrue(rbac.has_permission(self.finance, rbac.PRAGMADINARS_REFUND))
        self.assertFalse(rbac.has_permission(self.finance, rbac.ROLES_MANAGE))
        self.assertFalse(rbac.has_permission(self.finance, rbac.CRM_READ_PRIVATE))

    def test_analyst_cannot_read_private_crm_records(self):
        self._grant_system(self.plain, rbac.ANALYST)
        self.assertTrue(rbac.has_permission(self.plain, rbac.ANALYTICS_READ))
        self.assertFalse(rbac.has_permission(self.plain, rbac.CRM_READ_PRIVATE))
        self.assertFalse(rbac.has_permission(self.plain, rbac.USERS_READ))

    def test_revoking_a_role_keeps_the_history(self):
        self._grant_system(self.finance, rbac.ADMIN_FINANCE)
        rbac.revoke_role(self.finance, rbac.ADMIN_FINANCE, revoked_by=self.owner)
        self.assertFalse(rbac.has_permission(self.finance, rbac.PRAGMADINARS_REFUND))
        self.assertEqual(self.finance.role_grants.count(), 1)

    def test_a_deactivated_account_loses_access(self):
        self._make_ultra(self.owner)
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            rbac.require_permission(self.owner, rbac.AUDIT_READ)

    def test_sensitive_action_is_written_to_the_audit_log(self):
        rbac.log_audit(
            self.owner,
            "pragmadinars.refund",
            entity_type="PragmadinarLedger",
            entity_id=12,
            old_value={"amount": "-10"},
            new_value={"reference": "RFD-1"},
            reason="Incident",
        )
        row = AuditLog.objects.get()
        self.assertEqual(row.actor, self.owner)
        self.assertEqual(row.action, "pragmadinars.refund")
        self.assertEqual(row.entity_id, 12)
        self.assertEqual(row.reason, "Incident")


class CrmZeroBalanceTests(TestCase):
    """Base CRM must work with genereux plan and zero Pragmadinar balance."""

    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        self.supplier = make_supplier("Zero Pragmadinar CRM", plan="genereux")
        self.user = make_supplier_user(self.supplier, email="zerocrm@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.category = Category.objects.create(name_en="Boxes", level=1)

    def test_crm_routes_work_with_zero_balance(self):
        wallet = get_or_create_wallet(self.supplier)
        wallet.balance = Decimal("0")
        wallet.save(update_fields=["balance"])

        company = self.client.post(
            reverse("api_crm_companies"),
            {"name": "SARL Test"},
            format="json",
        )
        self.assertEqual(company.status_code, 201)

        contact = self.client.post(
            reverse("api_crm_contacts"),
            {"firstName": "Ali", "lastName": "B.", "companyId": company.data["id"]},
            format="json",
        )
        self.assertEqual(contact.status_code, 201)

        pipeline = self.client.get(reverse("api_crm_pipeline"))
        self.assertEqual(pipeline.status_code, 200)

        opportunity = Opportunity.objects.create(
            supplier=self.supplier,
            title="Deal test",
            company_id=company.data["id"],
        )
        moved = self.client.patch(
            reverse("api_crm_opportunity_detail", args=[opportunity.pk]),
            {"stage": "negotiation"},
            format="json",
        )
        self.assertEqual(moved.status_code, 200)

        activity = self.client.post(
            reverse("api_crm_activities"),
            {"title": "Relance", "opportunityId": opportunity.pk},
            format="json",
        )
        self.assertEqual(activity.status_code, 201)

        dashboard = self.client.get(reverse("api_supplier_crm_dashboard"))
        self.assertEqual(dashboard.status_code, 200)

    def test_crm_sync_from_order_with_zero_balance(self):
        wallet = get_or_create_wallet(self.supplier)
        wallet.balance = Decimal("0")
        wallet.save(update_fields=["balance"])
        buyer = make_buyer("crm-sync")
        product = Product.objects.create(
            supplier=self.supplier,
            name="Boite",
            category=self.category,
            subcategory=self.category,
        )
        order = Order.objects.create(
            buyer=buyer,
            supplier=self.supplier,
            product=product,
            quantity=10,
            unit_price=Decimal("5.00"),
        )
        contact = crm.sync_from_order(order)
        self.assertIsNotNone(contact.pk)
        self.assertTrue(
            crm.opportunities_for(self.supplier).filter(order=order).exists()
        )


class CrmIsolationTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        self.supplier_a = make_supplier("Alpha Pack")
        self.supplier_b = make_supplier("Beta Pack")
        self.user_a = make_supplier_user(self.supplier_a, email="a@example.com")
        self.user_b = make_supplier_user(self.supplier_b, email="b@example.com")
        self.buyer = make_buyer()
        self.client_a = APIClient()
        self.client_a.force_authenticate(self.user_a)
        self.client_b = APIClient()
        self.client_b.force_authenticate(self.user_b)

    def test_one_supplier_never_sees_another_companies(self):
        CrmCompany.objects.create(supplier=self.supplier_a, name="Client privé A")
        response = self.client_b.get(reverse("api_crm_companies"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

    def test_reading_another_supplier_record_returns_404(self):
        company = CrmCompany.objects.create(
            supplier=self.supplier_a, name="Client privé A"
        )
        response = self.client_b.get(
            reverse("api_crm_company_detail", args=[company.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_a_buyer_account_cannot_reach_the_crm(self):
        client = APIClient()
        client.force_authenticate(self.buyer)
        response = client.get(reverse("api_crm_companies"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "supplier_required")

    def test_anonymous_access_is_refused(self):
        response = APIClient().get(reverse("api_crm_pipeline"))
        self.assertEqual(response.status_code, 401)

    def test_pipeline_exposes_the_official_stages(self):
        response = self.client_a.get(reverse("api_crm_pipeline"))
        stages = [column["stage"] for column in response.data["columns"]]
        self.assertEqual(
            stages,
            [
                "prospect",
                "request_received",
                "quote_sent",
                "negotiation",
                "won",
                "lost",
            ],
        )

    def test_losing_an_opportunity_requires_a_reason(self):
        opportunity = Opportunity.objects.create(
            supplier=self.supplier_a, title="Test"
        )
        response = self.client_a.patch(
            reverse("api_crm_opportunity_detail", args=[opportunity.pk]),
            {"stage": "lost"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "lost_reason_required")

    def test_stage_change_is_kept_in_the_history(self):
        opportunity = Opportunity.objects.create(
            supplier=self.supplier_a, title="Test"
        )
        self.client_a.patch(
            reverse("api_crm_opportunity_detail", args=[opportunity.pk]),
            {"stage": "negotiation"},
            format="json",
        )
        activities = crm.activities_for(self.supplier_a).filter(
            kind="stage_change"
        )
        self.assertEqual(activities.count(), 1)
        self.assertIn("Négociation", activities.first().title)


class QuoteToCrmTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        self.category = Category.objects.create(name_en="Boxes", level=1)
        self.subcategory = Category.objects.create(
            name_en="Carton", level=2, parent=self.category
        )
        self.supplier = make_supplier("Gamma Pack")
        self.buyer = make_buyer("9")

    def _make_demand(self, kind="public"):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind=kind,
            category=self.category,
            subcategory=self.subcategory,
            quantity=1000,
            description="Besoin de cartons",
            indicative_budget=Decimal("50000"),
        )
        QuoteDemandSelection.objects.create(demand=demand, supplier=self.supplier)
        return demand

    def test_demand_creates_company_contact_opportunity_and_activity(self):
        demand = self._make_demand()
        opportunity = crm.sync_from_quote_demand(demand, self.supplier)

        self.assertEqual(opportunity.stage, Opportunity.Stage.REQUEST_RECEIVED)
        self.assertIsNotNone(opportunity.company_id)
        self.assertIsNotNone(opportunity.contact_id)
        self.assertEqual(opportunity.estimated_value, Decimal("50000"))
        self.assertTrue(
            crm.activities_for(self.supplier).filter(kind="system").exists()
        )

    def test_synchronisation_is_idempotent(self):
        demand = self._make_demand()
        first = crm.sync_from_quote_demand(demand, self.supplier)
        second = crm.sync_from_quote_demand(demand, self.supplier)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(crm.companies_for(self.supplier).count(), 1)

    def test_follow_up_reminders_are_scheduled_at_24h_and_48h(self):
        demand = self._make_demand()
        opportunity = crm.sync_from_quote_demand(demand, self.supplier)
        reminders = crm.activities_for(self.supplier).filter(
            kind="reminder", opportunity=opportunity
        )
        self.assertEqual(reminders.count(), 2)
        self.assertTrue(all(r.due_at is not None for r in reminders))

    def test_a_selected_supplier_sees_the_buyer_coordinates(self):
        demand = self._make_demand()
        opportunity = crm.sync_from_quote_demand(demand, self.supplier)
        payload = crm.contact_payload(opportunity.contact)
        self.assertFalse(payload["masked"])
        self.assertEqual(payload["email"], self.buyer.email)
        self.assertEqual(payload["buyerId"], self.buyer.id)

    def test_order_creates_named_contact_and_opportunity(self):
        self.buyer.first_name = "Mukhtar"
        self.buyer.save(update_fields=["first_name"])
        product = Product.objects.create(
            supplier=self.supplier,
            name="Coffret",
            category=self.category,
            subcategory=self.subcategory,
        )
        order = Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=product,
            quantity=100,
            unit_price=Decimal("10.00"),
        )
        contact = crm.sync_from_order(order)
        self.assertEqual(contact.first_name, "Mukhtar")
        self.assertEqual(crm.contact_payload(contact)["displayName"], "Mukhtar")
        self.assertTrue(
            crm.opportunities_for(self.supplier)
            .filter(source=Opportunity.Source.ORDER, contact=contact)
            .exists()
        )

    def test_sending_an_order_proposal_moves_to_quote_sent(self):
        self.buyer.first_name = "Ahmed"
        self.buyer.last_name = "Ezz"
        self.buyer.save(update_fields=["first_name", "last_name"])
        product = Product.objects.create(
            supplier=self.supplier,
            name="Coffret",
            category=self.category,
            subcategory=self.subcategory,
        )
        order = Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=product,
            quantity=20,
            unit_price=Decimal("15.00"),
        )
        crm.sync_from_order(order)
        opportunity = crm.mark_proposal_sent(
            self.supplier,
            order=order,
            note="Proposition envoyée.",
        )
        self.assertEqual(opportunity.stage, Opportunity.Stage.QUOTE_SENT)
        self.assertEqual(opportunity.contact.first_name, "Ahmed")

    def test_marking_order_won_moves_crm_opportunity_to_won(self):
        from user.models import OrderResponse

        product = Product.objects.create(
            supplier=self.supplier,
            name="Coffret",
            category=self.category,
            subcategory=self.subcategory,
        )
        order = Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=product,
            quantity=20,
            unit_price=Decimal("15.00"),
        )
        crm.sync_from_order(order)
        crm.mark_proposal_sent(self.supplier, order=order, note="Proposition envoyée.")
        OrderResponse.objects.create(
            order=order,
            supplier=self.supplier,
            message="OK",
            status=OrderResponse.Status.SUBMITTED,
        )
        order.status = Order.Status.WON
        order.save(update_fields=["status", "updated_at"])
        opp = crm.sync_order_outcome(order, outcome="won")
        self.assertIsNotNone(opp)
        opp.refresh_from_db()
        self.assertEqual(opp.stage, Opportunity.Stage.WON)
        self.assertEqual(opp.order_id, order.pk)
        self.assertEqual(opp.estimated_value, Decimal("300.00"))

    def test_manual_move_to_won_takes_value_from_linked_order(self):
        product = Product.objects.create(
            supplier=self.supplier,
            name="Coffret",
            category=self.category,
            subcategory=self.subcategory,
        )
        order = Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=product,
            quantity=1000,
            unit_price=Decimal("8.00"),
        )
        crm.sync_from_order(order)
        opp = crm.resolve_opportunity_for_order(order)
        self.assertIsNone(opp.estimated_value)

        crm.set_stage(opp, Opportunity.Stage.WON)
        opp.refresh_from_db()
        self.assertEqual(opp.estimated_value, Decimal("8000.00"))

    def test_boutique_rfq_creates_crm_contact(self):
        self.buyer.first_name = "Mukhtar"
        self.buyer.save(update_fields=["first_name"])
        rfq = RFQRequest.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            name="Mukhtar",
            company="XYZ holding",
            email=self.buyer.email,
            message="50 box",
        )
        contact = crm.sync_from_rfq(rfq)
        self.assertEqual(contact.company.name, "XYZ holding")
        self.assertEqual(contact.first_name, "Mukhtar")

    def test_boutique_visit_creates_prospect_opportunity(self):
        contact = crm.sync_from_boutique_visit(
            self.supplier, self.buyer, source="search"
        )
        opportunity = crm.opportunities_for(self.supplier).get(contact=contact)
        self.assertEqual(opportunity.stage, Opportunity.Stage.PROSPECT)
        self.assertEqual(opportunity.source, Opportunity.Source.STORE_VISIT)

    def test_product_favorite_creates_prospect_opportunity(self):
        product = Product.objects.create(
            supplier=self.supplier,
            name="Boite kraft",
            category=self.category,
            subcategory=self.subcategory,
        )
        contact = crm.sync_from_favorite(
            self.supplier, self.buyer, product=product
        )
        opportunity = crm.opportunities_for(self.supplier).get(contact=contact)
        self.assertEqual(opportunity.stage, Opportunity.Stage.PROSPECT)
        self.assertEqual(opportunity.source, Opportunity.Source.FAVORITE)
        self.assertIn("Boite kraft", opportunity.title)

    def test_a_paid_lead_keeps_the_buyer_coordinates_masked(self):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="public",
            category=self.category,
            subcategory=self.subcategory,
            quantity=500,
            description="Autre besoin",
        )
        opportunity = crm.sync_from_quote_demand(demand, self.supplier)
        payload = crm.contact_payload(opportunity.contact)
        self.assertTrue(payload["masked"])
        self.assertEqual(payload["email"], "")
        self.assertEqual(payload["phone"], "")

    def test_category_intent_reveals_buyer_coordinates(self):
        Product.objects.create(
            supplier=self.supplier,
            name="Carton plat",
            category=self.category,
            subcategory=self.subcategory,
        )
        contact = crm.sync_from_category_intent(
            self.supplier,
            self.buyer,
            self.subcategory,
            via="search_filter",
        )
        payload = crm.contact_payload(contact)
        self.assertFalse(payload["masked"])
        self.assertEqual(payload["email"], self.buyer.email)
        opportunity = crm.opportunities_for(self.supplier).get(contact=contact)
        self.assertEqual(opportunity.source, Opportunity.Source.CATEGORY_INTENT)

    def test_category_intent_skips_supplier_without_matching_catalog(self):
        other_sub = Category.objects.create(
            name_en="Film", level=2, parent=self.category
        )
        crm.sync_category_intent_for_search_results(
            self.buyer, [self.supplier.pk], other_sub
        )
        self.assertFalse(crm.contacts_for(self.supplier).exists())

    def test_buyer_interest_subcategory_syncs_matching_suppliers(self):
        Product.objects.create(
            supplier=self.supplier,
            name="Carton",
            category=self.category,
            subcategory=self.subcategory,
        )
        self.buyer.interested_subcategories.add(self.subcategory)
        crm.sync_suppliers_for_buyer_interest_subcategory(
            self.buyer, self.subcategory
        )
        contact = crm.contacts_for(self.supplier).get()
        self.assertTrue(crm.contact_payload(contact)["email"])


class CategoryIntentSearchApiTests(TestCase):
    def setUp(self):
        from user.search_vocabulary import invalidate_vocabulary_cache

        invalidate_vocabulary_cache()
        entitlements.seed_entitlements()
        self.category = Category.objects.create(name_en="Boxes", level=1)
        self.subcategory = Category.objects.create(
            name_en="Carton",
            name_fr="Carton ondulé",
            level=2,
            parent=self.category,
        )
        self.supplier = make_supplier("Search Pack")
        self.buyer = make_buyer("search-buyer")
        self.client = APIClient()
        Product.objects.create(
            supplier=self.supplier,
            name="Boite",
            category=self.category,
            subcategory=self.subcategory,
        )
        invalidate_vocabulary_cache()

    def test_text_search_without_lexicon_match_does_not_create_crm_contact(self):
        self.client.force_authenticate(self.buyer)
        before = crm.contacts_for(self.supplier).count()
        response = self.client.get(
            reverse("api_products"), {"q": "zzzzmotinconnu"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(crm.contacts_for(self.supplier).count(), before)

    def test_category_and_subcategory_filter_creates_crm_contact(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.get(
            reverse("api_products"),
            {"category": "Boxes", "subcategory_id": self.subcategory.pk},
        )
        self.assertEqual(response.status_code, 200)
        contact = crm.contacts_for(self.supplier).get()
        self.assertEqual(crm.contact_payload(contact)["email"], self.buyer.email)

    def test_subcategory_keyword_in_query_creates_crm_contact(self):
        from user.search_vocabulary import resolve_subcategories_from_query

        resolved = resolve_subcategories_from_query("carton")
        self.assertIn(self.subcategory.pk, [s.pk for s in resolved])

        self.client.force_authenticate(self.buyer)
        response = self.client.get(reverse("api_products"), {"q": "carton"})
        self.assertEqual(response.status_code, 200)
        contact = crm.contacts_for(self.supplier).get()
        self.assertEqual(crm.contact_payload(contact)["email"], self.buyer.email)

    def test_search_vocabulary_api_returns_descriptor(self):
        response = self.client.get(reverse("api_search_vocabulary"), {"summary": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("stats", response.data)
        self.assertIn("sources", response.data)
        self.assertIsNone(response.data.get("entries"))


class AnalyticsReportTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog
        from user.platform_config import seed_settings

        seed_pragmadinar_catalog()
        seed_settings()
        self.supplier = make_supplier("Intel Pack")
        self.category = Category.objects.create(name_en="Boxes", level=1)
        self.subcategory = Category.objects.create(
            name_en="Corrugated", parent=self.category, level=2
        )
        Product.objects.create(
            supplier=self.supplier,
            name="Intel box",
            category=self.category,
            subcategory=self.subcategory,
        )
        self.user = make_supplier_user(self.supplier, email="intel@example.com")
        credit_wallet(self.supplier, 100, wallet_category="analytics")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _wallet(self):
        return get_or_create_wallet(self.supplier, category="analytics")

    @staticmethod
    def _cost(code):
        return get_service(code).cost_pragmadinar

    def _make_peer_with_same_subcategory(self, name: str, email: str):
        peer = make_supplier(name, email=email)
        Product.objects.create(
            supplier=peer,
            name=f"{name} product",
            category=self.category,
            subcategory=self.subcategory,
        )
        return peer

    def _purchase(self, report_type, **params):
        return self.client.post(
            reverse("api_analytics_purchase"),
            {"reportType": report_type, **params},
            format="json",
        )

    def test_catalogue_shows_price_dinars_and_balance_after(self):
        response = self.client.get(reverse("api_analytics_catalog"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["currentMonthIsFree"])
        monthly = next(
            row for row in response.data["reports"] if row["code"] == "intel.report_monthly"
        )
        cost = self._cost("intel.report_monthly")
        self.assertEqual(monthly["costPragmadinar"], str(cost))
        self.assertEqual(monthly["costDzd"], str(pragmadinar_to_dzd(cost)))
        self.assertEqual(Decimal(monthly["balanceAfter"]), 100 - cost)
        self.assertTrue(monthly["affordable"])

    def test_current_month_dashboard_debits_the_catalog_price(self):
        response = self.client.get(reverse("api_analytics_current_month"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["free"])
        self.assertEqual(self._wallet().balance, 100 - self._cost("intel.dashboard_current"))

    def test_current_month_dashboard_requires_balance(self):
        wallet = self._wallet()
        wallet.balance = Decimal("0")
        wallet.save(update_fields=["balance"])
        response = self.client.get(reverse("api_analytics_current_month"))
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "insufficient_pragmadinars")

    def test_buying_a_monthly_report_debits_the_listed_price(self):
        cost = self._cost("intel.report_monthly")
        response = self._purchase("intel.report_monthly", year=2026, month=1)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["costPragmadinar"], str(cost))
        self.assertEqual(self._wallet().balance, 100 - cost)

    def test_buying_the_same_report_twice_does_not_debit_twice(self):
        first = self._purchase("intel.report_monthly", year=2026, month=1)
        second = self._purchase("intel.report_monthly", year=2026, month=1)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["alreadyOwned"])
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(self._wallet().balance, 100 - self._cost("intel.report_monthly"))

    def test_annual_report_debits_its_catalog_price(self):
        cost = self._cost("intel.report_annual")
        response = self._purchase("intel.report_annual", year=2026, month=1)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["costPragmadinar"], str(cost))
        self.assertEqual(self._wallet().balance, 100 - cost)

    def test_insufficient_balance_blocks_the_purchase(self):
        wallet = self._wallet()
        wallet.balance = self._cost("intel.report_annual")
        wallet.save(update_fields=["balance"])
        self._purchase("intel.report_annual", year=2026, month=1)
        response = self._purchase("intel.report_quarterly", year=2026, month=2)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.data["code"], "insufficient_pragmadinars")

    def test_empty_analytics_wallet_is_covered_by_other_wallets(self):
        wallet = self._wallet()
        wallet.balance = Decimal("0")
        wallet.save(update_fields=["balance"])
        credit_wallet(self.supplier, 100, wallet_category="visibility")
        cost = self._cost("intel.report_monthly")
        response = self._purchase("intel.report_monthly", year=2026, month=1)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self._wallet().balance, Decimal("0"))
        self.assertEqual(get_or_create_wallet(self.supplier).balance, 100 - cost)

    def test_current_month_is_never_sold_as_a_previous_month(self):
        from django.utils import timezone

        now = timezone.now()
        response = self._purchase(
            "intel.dashboard_previous", year=now.year, month=now.month
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "current_month_is_free")
        self.assertEqual(self._wallet().balance, Decimal("100"))

    def test_benchmark_is_refused_below_the_anonymity_threshold(self):
        response = self._purchase("intel.benchmark_supplier", year=2026, month=1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "insufficient_data")
        self.assertEqual(int(response.data["required"]), 5)
        # Nothing was charged for a result that could not be anonymized.
        self.assertEqual(self._wallet().balance, Decimal("100"))

    def test_benchmark_is_anonymous_once_the_threshold_is_met(self):
        for index in range(6):
            self._make_peer_with_same_subcategory(
                f"Peer {index}", email=f"peer{index}@example.com"
            )
        response = self._purchase("intel.benchmark_supplier", year=2026, month=1)
        self.assertEqual(response.status_code, 201)
        benchmark = response.data["payload"]["benchmark"]
        self.assertTrue(benchmark["anonymized"])
        self.assertGreaterEqual(benchmark["peerCount"], 5)
        self.assertNotIn("Peer 0", str(response.data))

    def test_threshold_is_configurable(self):
        set_setting("benchmark_min_suppliers", "2")
        self._make_peer_with_same_subcategory("Peer A", email="peera@example.com")
        self._make_peer_with_same_subcategory("Peer B", email="peerb@example.com")
        response = self._purchase("intel.benchmark_supplier", year=2026, month=1)
        self.assertEqual(response.status_code, 201)

    def test_reports_are_scoped_to_their_owner(self):
        created = self._purchase("intel.report_monthly", year=2026, month=1)
        other = make_supplier("Other Pack", email="other@example.com")
        other_user = make_supplier_user(other, email="otheruser@example.com")
        client = APIClient()
        client.force_authenticate(other_user)
        response = client.get(
            reverse("api_analytics_report_detail", args=[created.data["id"]])
        )
        self.assertEqual(response.status_code, 404)

    def test_purchased_report_stays_readable_for_free(self):
        created = self._purchase("intel.report_monthly", year=2026, month=1)
        balance = self._wallet().balance
        response = self.client.get(
            reverse("api_analytics_report_detail", args=[created.data["id"]])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("payload", response.data)
        self.assertEqual(self._wallet().balance, balance)

    def test_period_report_requires_explicit_dates(self):
        response = self._purchase("intel.market_period")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "period_required")


class PakDatingConsentTests(TestCase):
    def setUp(self):
        from user.platform_config import set_setting

        set_setting("pak_dating_enabled", "true")
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        self.supplier = make_supplier("Delta Pack")
        self.supplier_user = make_supplier_user(self.supplier, email="d@example.com")
        self.buyer = make_buyer("22")
        credit_wallet(self.supplier, 50, wallet_category="promotion")
        self.cost = get_service(PAK_DATING_CODE).cost_pragmadinar
        self.supplier_client = APIClient()
        self.supplier_client.force_authenticate(self.supplier_user)
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)

    def _wallet(self):
        return get_or_create_wallet(self.supplier, category="promotion")

    def _request_introduction(self):
        return self.supplier_client.post(
            reverse("api_pak_dating_request"),
            {"buyerId": self.buyer.pk, "signal": "favori"},
            format="json",
        )

    def test_identity_is_not_revealed_before_consent(self):
        response = self._request_introduction()
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["buyer"]["revealed"])
        self.assertEqual(response.data["buyer"]["companyName"], "Acheteur confidentiel")
        self.assertNotIn(self.buyer.email, str(response.data))

    def test_request_only_reserves_the_price(self):
        self._request_introduction()
        wallet = self._wallet()
        self.assertEqual(wallet.balance, Decimal("50"))
        self.assertEqual(wallet.held_total, self.cost)

    def test_acceptance_reveals_the_data_and_debits_the_supplier(self):
        self._request_introduction()
        record = ConsentRecord.objects.get()
        response = self.buyer_client.post(
            reverse("api_buyer_consent_decide", args=[record.pk]),
            {"decision": "accept"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        wallet = self._wallet()
        self.assertEqual(wallet.balance, 50 - self.cost)
        self.assertEqual(wallet.held_total, Decimal("0"))

        listing = self.supplier_client.get(reverse("api_supplier_pak_dating"))
        revealed = listing.data["results"][0]["buyer"]
        self.assertTrue(revealed["revealed"])
        self.assertEqual(revealed["email"], self.buyer.email)

    def test_refusal_releases_the_reservation_and_reveals_nothing(self):
        self._request_introduction()
        record = ConsentRecord.objects.get()
        self.buyer_client.post(
            reverse("api_buyer_consent_decide", args=[record.pk]),
            {"decision": "refuse"},
            format="json",
        )
        wallet = self._wallet()
        self.assertEqual(wallet.balance, Decimal("50"))
        self.assertEqual(wallet.held_total, Decimal("0"))

        listing = self.supplier_client.get(reverse("api_supplier_pak_dating"))
        self.assertFalse(listing.data["results"][0]["buyer"]["revealed"])

    def test_a_supplier_cannot_decide_for_the_buyer(self):
        self._request_introduction()
        record = ConsentRecord.objects.get()
        response = self.supplier_client.post(
            reverse("api_buyer_consent_decide", args=[record.pk]),
            {"decision": "accept"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        record.refresh_from_db()
        self.assertEqual(record.status, ConsentRecord.Status.PENDING)

    def test_consent_decision_is_audited(self):
        self._request_introduction()
        record = ConsentRecord.objects.get()
        self.buyer_client.post(
            reverse("api_buyer_consent_decide", args=[record.pk]),
            {"decision": "accept"},
            format="json",
        )
        self.assertTrue(
            AuditLog.objects.filter(action="consent.granted", entity_id=record.pk).exists()
        )

    def test_pak_dating_is_refused_once_pragmadinar_are_all_held(self):
        wallet = self._wallet()
        wallet.held_total = wallet.balance
        wallet.save(update_fields=["held_total"])
        self.assertEqual(entitlements.plan_code_for(self.supplier), "genereux")
        response = self._request_introduction()
        self.assertIn(response.status_code, (400, 402, 403))
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_pak_dating_disabled_by_platform_flag(self):
        from user.platform_config import set_setting

        set_setting("pak_dating_enabled", "false")
        response = self._request_introduction()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "feature_disabled")
        self.assertEqual(ConsentRecord.objects.count(), 0)

    def test_insufficient_balance_blocks_the_introduction(self):
        PragmadinarLedger.objects.all().delete()
        wallet = self._wallet()
        wallet.balance = Decimal("0")
        wallet.save(update_fields=["balance"])
        response = self._request_introduction()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "insufficient_pragmadinars")


class PublicUnlockGateTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog
        from user.platform_config import seed_settings

        seed_pragmadinar_catalog()
        seed_settings()
        self.supplier = make_supplier("Unlock Co")
        self.user = make_supplier_user(self.supplier, email="unlock@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        credit_wallet(self.supplier, 50)

    def _public_demand(self, name):
        from django.utils import timezone

        from user.models import Category, QuoteDemand

        category = Category.objects.create(name_en=name, level=1)
        demand = QuoteDemand.objects.create(
            buyer=make_buyer(name.lower()),
            kind="public",
            category=category,
            subcategory=category,
            quantity=10,
            description="Test",
            visible_on_market=True,
            market_published_at=timezone.now(),
        )
        return category, demand

    def test_public_participation_is_free_for_eligible_supplier(self):
        from user.models import PragmadinarLedger, QuoteDemandEngagement

        category, demand = self._public_demand("Boxes")
        self.supplier.interested_categories.add(category)
        ledger_count = PragmadinarLedger.objects.count()
        response = self.client.post(
            reverse("api_supplier_opportunity_unlock", args=[demand.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_or_create_wallet(self.supplier).balance, Decimal("50"))
        self.assertEqual(PragmadinarLedger.objects.count(), ledger_count)
        engagement = QuoteDemandEngagement.objects.get(demand=demand, supplier=self.supplier)
        self.assertEqual(engagement.source, QuoteDemandEngagement.Source.PUBLIC_JOINED)
        self.assertEqual(engagement.paid_amount, 0)

    def test_public_participation_requires_matching_category(self):
        _category, demand = self._public_demand("Tubes")
        response = self.client.post(
            reverse("api_supplier_opportunity_unlock", args=[demand.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "category_mismatch")


class QuoteMarketWorkflowTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog
        from user.platform_config import seed_settings

        seed_pragmadinar_catalog()
        seed_settings()
        self.category = Category.objects.create(name_en="Film", level=1)
        self.buyer = make_buyer("market")
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)
        self.supplier = make_supplier("Market Co")
        self.supplier.interested_subcategories.add(self.category)
        self.supplier_client = APIClient()
        self.supplier_client.force_authenticate(
            make_supplier_user(self.supplier, email="market@example.com")
        )

    def test_public_with_selection_hidden_from_opportunities(self):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="public",
            category=self.category,
            subcategory=self.category,
            quantity=5,
            description="Ciblée",
            visible_on_market=False,
            market_removed_reason="selected_supplier",
        )
        QuoteDemandSelection.objects.create(demand=demand, supplier=self.supplier)
        response = self.supplier_client.get(reverse("api_supplier_opportunities"))
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.data["results"]]
        self.assertNotIn(demand.id, ids)

    def test_open_public_expires_to_admin_queue(self):
        from datetime import timedelta

        from django.utils import timezone

        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="public",
            category=self.category,
            subcategory=self.category,
            quantity=5,
            description="Ancienne",
            visible_on_market=True,
            market_published_at=timezone.now() - timedelta(days=8),
        )
        self.supplier_client.get(reverse("api_supplier_opportunities"))
        demand.refresh_from_db()
        self.assertFalse(demand.visible_on_market)
        self.assertTrue(demand.admin_queue)
        self.assertEqual(demand.market_removed_reason, "age_limit")

    def test_buyer_can_relaunch_off_market_demand(self):
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="public",
            category=self.category,
            subcategory=self.category,
            quantity=5,
            description="Relance",
            visible_on_market=False,
            admin_queue=True,
            market_removed_reason="age_limit",
        )
        response = self.buyer_client.post(
            reverse("api_quote_demand_relaunch", args=[demand.pk])
        )
        self.assertEqual(response.status_code, 200)
        demand.refresh_from_db()
        self.assertTrue(demand.visible_on_market)
        self.assertFalse(demand.admin_queue)
        opp = self.supplier_client.get(reverse("api_supplier_opportunities"))
        ids = [row["id"] for row in opp.data["results"]]
        self.assertIn(demand.id, ids)

    def test_buyer_converts_won_proposal_to_order(self):
        from decimal import Decimal

        from user.models import Order, Product, QuoteProposal

        product = Product.objects.create(
            supplier=self.supplier,
            name="Film stretch",
            description="Devis",
            category=self.category,
            subcategory=self.category,
            price=Decimal("12.00"),
        )
        demand = QuoteDemand.objects.create(
            buyer=self.buyer,
            kind="private",
            category=self.category,
            subcategory=self.category,
            quantity=10,
            description="Besoin urgent",
        )
        QuoteDemandSelection.objects.create(demand=demand, supplier=self.supplier)
        proposal = QuoteProposal.objects.create(
            demand=demand,
            supplier=self.supplier,
            unit_price=Decimal("11.50"),
            confirmed_quantity=10,
            lead_time_days=7,
            moq=5,
            message="OK pour livraison",
            status=QuoteProposal.Status.SENT,
        )
        decide = self.buyer_client.post(
            reverse("api_quote_demand_decide", args=[demand.pk]),
            {"proposalId": proposal.pk, "decision": "accept"},
            format="json",
        )
        self.assertEqual(decide.status_code, 200)
        order_id = decide.data["orderId"]
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.buyer_id, self.buyer.id)
        self.assertEqual(order.supplier_id, self.supplier.id)
        self.assertEqual(order.product_id, product.id)
        self.assertEqual(order.quantity, 10)
        self.assertEqual(order.quote_proposal_id, proposal.pk)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.commercial_snapshot["proposalId"], proposal.pk)

        again = self.buyer_client.post(
            reverse("api_quote_demand_convert_to_order", args=[demand.pk]),
            {"proposalId": proposal.pk},
            format="json",
        )
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.data["alreadyExists"])
        self.assertEqual(again.data["order"]["id"], order_id)

    def test_buyer_confirms_order_from_rfq_quote_response(self):
        from decimal import Decimal

        product = Product.objects.create(
            supplier=self.supplier,
            name="Coffret cartonné",
            description="Emballage",
            category=self.category,
            subcategory=self.category,
            price=Decimal("10.00"),
        )
        rfq = RFQRequest.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=product,
            name="Mukhtar",
            email=self.buyer.email,
            message="50 box",
        )
        response = QuoteResponse.objects.create(
            quote_request=rfq,
            supplier=self.supplier,
            message="Proposition commerciale",
            proposed_price=Decimal("9.50"),
            lead_time_days=14,
        )
        prefill = self.buyer_client.get(
            reverse("api_quote_response_order_prefill", args=[response.pk])
        )
        self.assertEqual(prefill.status_code, 200)
        self.assertEqual(prefill.data["productId"], product.id)

        confirm = self.buyer_client.post(
            reverse("api_quote_response_confirm_order", args=[response.pk]),
            {"quantity": 100, "wilaya": "oran", "specifications": "50 box"},
            format="json",
        )
        self.assertEqual(confirm.status_code, 201)
        order = Order.objects.get(pk=confirm.data["order"]["id"])
        self.assertEqual(order.quantity, 100)
        self.assertEqual(order.quote_response_id, response.pk)
        rfq.refresh_from_db()
        response.refresh_from_db()
        self.assertEqual(rfq.status, "accepted")
        self.assertEqual(response.status, "accepted")


class CampaignAndTemplateTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog
        from user.platform_config import seed_settings

        seed_pragmadinar_catalog()
        seed_settings()
        self.supplier = make_supplier("Promo Co")
        self.user = make_supplier_user(self.supplier, email="promo@example.com")
        credit_wallet(self.supplier, 20)
        credit_wallet(self.supplier, 20, wallet_category="promotion")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_campaign_requires_a_budget(self):
        response = self.client.post(
            reverse("api_supplier_campaigns"),
            {"name": "Mega", "format": "vitrine_mega", "budgetPragmadinar": 0},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_campaign_separates_organic_and_sponsored_views(self):
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {
                "name": "Vitrine Extra",
                "format": "vitrine_extra",
                "budgetPragmadinar": 5,
                "viewCap": 10,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["sponsoredViews"], 0)
        self.assertEqual(created.data["organicViews"], 0)
        self.assertEqual(Decimal(created.data["budgetPragmadinar"]), Decimal("5"))

        self.client.patch(
            reverse("api_supplier_campaign_detail", args=[created.data["id"]]),
            {"status": "active"},
            format="json",
        )
        for _ in range(10):
            hit = self.client.post(
                reverse("api_supplier_campaign_hit", args=[created.data["id"]]),
                {"kind": "visit"},
                format="json",
            )
            self.assertEqual(hit.status_code, 200)
        detail = self.client.get(
            reverse("api_supplier_campaign_detail", args=[created.data["id"]])
        )
        self.assertEqual(detail.data["sponsoredVisits"], 10)
        self.assertEqual(detail.data["sponsoredViews"], 0)
        self.assertEqual(detail.data["organicViews"], 0)
        service = get_service("visibility.vitrine_extra")
        units = 10 // max(int(service.unit_quantity or 1), 1)
        self.assertEqual(
            get_or_create_wallet(self.supplier, category="promotion").balance,
            Decimal("20") - units * service.cost_pragmadinar,
        )

    def test_campaign_name_is_optional(self):
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": "card_passport", "budgetPragmadinar": 5, "objective": "views",
             "goalCount": 20},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.data["name"].startswith("Carte Passport"))
        self.assertEqual(created.data["goalTarget"], 20)

    def test_conversation_banner_is_a_separate_format(self):
        listing = self.client.get(reverse("api_supplier_campaigns"))
        formats = {item["value"]: item for item in listing.data["formats"]}
        self.assertEqual(formats["banner"]["placement"], "search_results_bottom")
        self.assertEqual(formats["banner_conversation"]["placement"], "buyer_conversations")
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": "banner_conversation", "budgetPragmadinar": 5, "objective": "views",
             "goalCount": 10},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["serviceCode"], "promo.sponsored_banner_conversation")

    def test_promotion_orders_cover_keywords_and_subcategory(self):
        from user.models import Category, Product

        listing = self.client.get(reverse("api_supplier_campaigns"))
        formats = {item["value"] for item in listing.data["formats"]}
        self.assertIn("keyword_reservation", formats)
        self.assertIn("priority_subcategory", formats)

        missing = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": "keyword_reservation", "budgetPragmadinar": 5, "objective": "duration", "durationDays": 7},
            format="json",
        )
        self.assertEqual(missing.status_code, 400)

        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {
                "format": "keyword_reservation",
                "budgetPragmadinar": 999,
                "objective": "views",
                "goalCount": 10,
                "keywords": ["bouteille", "PET"],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data["serviceCode"], "promo.keyword_reservation")
        self.assertEqual(created.data["keywords"], ["bouteille", "PET"])
        self.assertEqual(created.data["goalTarget"], 10)
        self.assertEqual(Decimal(created.data["budgetPragmadinar"]), Decimal("3.00"))

        parent = Category.objects.create(name_en="Bottles", name_fr="Flacons", slug="flacons-promo", level=1)
        child = Category.objects.create(
            name_en="PET bottle",
            name_fr="Bouteille PET",
            slug="bouteille-pet-promo",
            level=2,
            parent=parent,
        )
        Product.objects.create(
            supplier=self.supplier,
            category=parent,
            subcategory=child,
            name="Bouteille",
            description="PET",
        )
        Product.objects.create(
            supplier=self.supplier,
            category=parent,
            subcategory=child,
            name="Bouteille 2",
            description="PET",
        )
        reserved = self.client.post(
            reverse("api_supplier_campaigns"),
            {
                "format": "priority_subcategory",
                "budgetPragmadinar": 999,
                "objective": "views",
                "goalCount": 10,
                "subcategoryId": child.pk,
            },
            format="json",
        )
        self.assertEqual(reserved.status_code, 201, reserved.content)
        self.assertEqual(reserved.data["subcategoryId"], child.pk)
        self.assertEqual(reserved.data["subcategoryName"], "Bouteille PET")
        self.assertEqual(Decimal(reserved.data["budgetPragmadinar"]), Decimal("8.00"))

    def _active_banner(self, fmt="banner"):
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": fmt, "budgetPragmadinar": 5, "objective": "views", "goalCount": 50},
            format="json",
        )
        self.client.patch(
            reverse("api_supplier_campaign_detail", args=[created.data["id"]]),
            {"status": "active"},
            format="json",
        )
        return created.data["id"]

    def test_public_banner_endpoint_serves_active_campaign_per_placement(self):
        from django.core.cache import cache

        cache.clear()
        public = APIClient()
        url = reverse("api_sponsored_banner")
        self.assertIsNone(public.get(url, {"placement": "search_results_bottom"}).data)

        search_id = self._active_banner("banner")
        conversation_id = self._active_banner("banner_conversation")
        search = public.get(url, {"placement": "search_results_bottom"})
        self.assertEqual(search.data["campaignId"], search_id)
        self.assertEqual(search.data["supplier"]["companyName"], "Promo Co")
        conversation = public.get(url, {"placement": "buyer_conversations"})
        self.assertEqual(conversation.data["campaignId"], conversation_id)
        self.assertEqual(public.get(url, {"placement": "nowhere"}).status_code, 400)

    def test_public_banner_hits_are_deduped_and_skip_the_owner(self):
        from django.core.cache import cache

        cache.clear()
        campaign_id = self._active_banner("banner")
        hit_url = reverse("api_sponsored_banner_hit", args=[campaign_id])

        buyer = APIClient()
        buyer.force_authenticate(make_buyer("banner"))
        self.assertTrue(buyer.post(hit_url, {"kind": "view"}, format="json").data["billed"])
        self.assertFalse(buyer.post(hit_url, {"kind": "view"}, format="json").data["billed"])
        self.assertTrue(buyer.post(hit_url, {"kind": "visit"}, format="json").data["billed"])
        self.assertFalse(self.client.post(hit_url, {"kind": "view"}, format="json").data["billed"])

        campaign = self.supplier.campaigns.get(pk=campaign_id)
        self.assertEqual(campaign.views_consumed, 1)
        self.assertEqual(campaign.visits_consumed, 1)

    def test_banner_context_scope_defaults_to_all_and_is_banner_only(self):
        listing = self.client.get(reverse("api_supplier_campaigns"))
        self.assertEqual(
            [item["value"] for item in listing.data["contextScopes"]],
            ["all", "supplier_categories"],
        )
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": "vitrine_extra", "budgetPragmadinar": 5, "objective": "views",
             "goalCount": 10, "contextScope": "supplier_categories"},
            format="json",
        )
        self.assertEqual(created.data["contextScope"], "all")
        patched = self.client.patch(
            reverse("api_supplier_campaign_detail", args=[created.data["id"]]),
            {"contextScope": "supplier_categories"},
            format="json",
        )
        self.assertEqual(patched.status_code, 400)
        banner_id = self._active_banner("banner")
        patched = self.client.patch(
            reverse("api_supplier_campaign_detail", args=[banner_id]),
            {"contextScope": "supplier_categories"},
            format="json",
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data["contextScope"], "supplier_categories")
        invalid = self.client.patch(
            reverse("api_supplier_campaign_detail", args=[banner_id]),
            {"contextScope": "everywhere"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)

    def test_category_scoped_banner_only_shows_in_supplier_categories(self):
        from user.models import Message

        boxes = Category.objects.create(name_en="Boxes", name_fr="Boîtes", level=1)
        carton = Category.objects.create(name_en="Carton", level=2, parent=boxes)
        bottles = Category.objects.create(name_en="Bottles", name_fr="Bouteilles", level=1)
        glass = Category.objects.create(name_en="Glass bottles", level=2, parent=bottles)
        self.supplier.interested_subcategories.add(carton)
        other = make_supplier("Verre Co")
        Product.objects.create(
            supplier=other, name="Flacon verre ambre", category=bottles, subcategory=glass
        )

        listing = self.client.get(reverse("api_supplier_campaigns"))
        self.assertEqual(listing.data["supplierCategories"], ["Boîtes"])

        campaign_id = self._active_banner("banner")
        self.client.patch(
            reverse("api_supplier_campaign_detail", args=[campaign_id]),
            {"contextScope": "supplier_categories"},
            format="json",
        )
        public = APIClient()
        url = reverse("api_sponsored_banner")
        placement = "search_results_bottom"
        self.assertIsNone(public.get(url, {"placement": placement}).data)
        self.assertIsNone(public.get(url, {"placement": placement, "category": "Bottles"}).data)
        self.assertIsNone(public.get(url, {"placement": placement, "q": "flacon verre"}).data)
        in_category = public.get(url, {"placement": placement, "category": "Boxes"})
        self.assertEqual(in_category.data["campaignId"], campaign_id)
        in_subcategory = public.get(url, {"placement": placement, "subcategory_id": carton.id})
        self.assertEqual(in_subcategory.data["campaignId"], campaign_id)

        conversation_id = self._active_banner("banner_conversation")
        self.client.patch(
            reverse("api_supplier_campaign_detail", args=[conversation_id]),
            {"contextScope": "supplier_categories"},
            format="json",
        )
        buyer = make_buyer("ctx")
        buyer_client = APIClient()
        buyer_client.force_authenticate(buyer)
        conversations = {"placement": "buyer_conversations"}
        self.assertIsNone(buyer_client.get(url, conversations).data)
        Message.objects.create(supplier=other, buyer=buyer, sender=buyer, content="Bonjour")
        self.assertIsNone(buyer_client.get(url, conversations).data)
        buyer.interested_subcategories.add(carton)
        self.assertEqual(buyer_client.get(url, conversations).data["campaignId"], conversation_id)

    def test_duration_objective_starts_on_activation(self):
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": "vitrine_extra", "budgetPragmadinar": 5, "objective": "duration",
             "durationDays": 7},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertIsNone(created.data["startsAt"])
        self.assertIsNone(created.data["goalTarget"])
        activated = self.client.patch(
            reverse("api_supplier_campaign_detail", args=[created.data["id"]]),
            {"status": "active"},
            format="json",
        )
        self.assertEqual((activated.data["endsAt"] - activated.data["startsAt"]).days, 7)

    def test_duration_objective_requires_days(self):
        response = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": "vitrine_extra", "budgetPragmadinar": 5, "objective": "duration"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_visits_objective_ignores_views_and_completes_at_goal(self):
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {"format": "banner", "budgetPragmadinar": 10, "objective": "visits",
             "goalCount": 2},
            format="json",
        )
        campaign_id = created.data["id"]
        self.client.patch(
            reverse("api_supplier_campaign_detail", args=[campaign_id]),
            {"status": "active"},
            format="json",
        )
        hit_url = reverse("api_supplier_campaign_hit", args=[campaign_id])
        self.client.post(hit_url, {"kind": "view"}, format="json")
        self.client.post(hit_url, {"kind": "visit"}, format="json")
        last = self.client.post(hit_url, {"kind": "visit"}, format="json")
        self.assertEqual(last.data["goalProgress"], 2)
        self.assertEqual(last.data["sponsoredViews"], 1)
        self.assertEqual(last.data["status"], "completed")

    def test_campaign_stops_at_the_view_cap(self):
        created = self.client.post(
            reverse("api_supplier_campaigns"),
            {
                "name": "Plafond",
                "format": "banner",
                "budgetPragmadinar": 10,
                "viewCap": 2,
            },
            format="json",
        )
        self.client.patch(
            reverse("api_supplier_campaign_detail", args=[created.data["id"]]),
            {"status": "active"},
            format="json",
        )
        self.client.post(reverse("api_supplier_campaign_hit", args=[created.data["id"]]))
        self.client.post(reverse("api_supplier_campaign_hit", args=[created.data["id"]]))
        third = self.client.post(
            reverse("api_supplier_campaign_hit", args=[created.data["id"]])
        )
        self.assertFalse(third.data["billed"])
        self.assertEqual(third.data["sponsoredViews"], 2)

    def test_extra_template_debits_its_catalog_price(self):
        cost = get_service("promo.proposal_template").cost_pragmadinar
        preview = self.client.get(reverse("api_extra_template"))
        self.assertEqual(preview.data["proposal"]["costPragmadinar"], str(cost))
        self.assertEqual(preview.data["proposal"]["costDzd"], str(pragmadinar_to_dzd(cost)))

        bought = self.client.post(
            reverse("api_extra_template"),
            {"kind": "proposal"},
            format="json",
        )
        self.assertEqual(bought.status_code, 201)
        promotion = get_or_create_wallet(self.supplier, category="promotion")
        self.assertEqual(promotion.balance, 20 - cost)
        self.assertTrue(bought.data["template"]["paid"])

    def _empty_wallets(self):
        for category in ("visibility", "promotion"):
            wallet = get_or_create_wallet(self.supplier, category=category)
            wallet.balance = Decimal("0")
            wallet.save(update_fields=["balance"])

    def test_extra_template_is_blocked_without_balance(self):
        self._empty_wallets()
        response = self.client.post(
            reverse("api_extra_template"), {"kind": "quote"}, format="json"
        )
        self.assertEqual(response.status_code, 402)

    def test_exhausted_wallet_cannot_prepare_a_campaign(self):
        from user.models import Campaign

        self._empty_wallets()
        self.assertEqual(entitlements.plan_code_for(self.supplier), "genereux")
        response = self.client.post(
            reverse("api_supplier_campaigns"),
            {"name": "X", "format": "banner", "budgetPragmadinar": 5},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Campaign.objects.filter(supplier=self.supplier).exists())
        self.assertEqual(get_or_create_wallet(self.supplier, category="promotion").balance, Decimal("0"))

    def test_new_campaign_starts_as_a_draft_without_debit(self):
        response = self.client.post(
            reverse("api_supplier_campaigns"),
            {"name": "X", "format": "banner", "budgetPragmadinar": 5},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "draft")
        promotion = get_or_create_wallet(self.supplier, category="promotion")
        self.assertEqual(promotion.balance, Decimal("20"))

    def test_cannot_save_extra_proposal_template_without_purchase(self):
        from user.api.serializers import ensure_supplier_settings

        settings = ensure_supplier_settings(self.supplier)
        settings.proposal_templates = [
            {
                "id": "default",
                "name": "Modèle 1",
                "isDefault": True,
                "blocks": [{"id": "b1", "type": "text", "content": "Hello"}],
            }
        ]
        settings.save()
        response = self.client.patch(
            reverse("api_supplier_settings"),
            {
                "proposalTemplates": [
                    settings.proposal_templates[0],
                    {
                        "id": "free_rider",
                        "name": "Modèle 2",
                        "isDefault": False,
                        "blocks": [{"id": "b2", "type": "text", "content": "Nope"}],
                    },
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data.get("code"), "extra_template_payment_required")


class OrderAmountEditTests(TestCase):
    def setUp(self):
        self.supplier = make_supplier("Prix Co")
        self.user = make_supplier_user(self.supplier, email="prix@example.com")
        self.buyer = make_buyer("amount")
        self.category = Category.objects.create(name_en="Boxes", level=1)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _order(self, *, price=None, price_max=None, status=Order.Status.PENDING):
        product = Product.objects.create(
            supplier=self.supplier,
            name="Boîte kraft",
            category=self.category,
            price=price,
            price_max=price_max,
        )
        order = Order.objects.create(
            buyer=self.buyer,
            supplier=self.supplier,
            product=product,
            quantity=500,
            unit_price=product.fixed_unit_price or 0,
            status=status,
            origin=Order.Origin.PRODUCT_PAGE,
        )
        from user.commission import register_order_commission

        register_order_commission(order)
        return order

    def _patch(self, order, payload, client=None):
        return (client or self.client).patch(
            reverse("api_supplier_order_amount", args=[order.pk]), payload, format="json"
        )

    def test_supplier_sets_amount_when_price_is_a_range(self):
        from user.models import Message, OrderCommission

        order = self._order(price=Decimal("10"), price_max=Decimal("15"))
        listing = self.client.get(reverse("api_supplier_orders"))
        row = next(item for item in listing.data if item["id"] == order.pk)
        self.assertTrue(row["amountEditable"])
        self.assertEqual(row["productPriceRange"], {"min": "10.00", "max": "15.00"})

        response = self._patch(order, {"unitPrice": "12,50", "note": "Prix négocié"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unitPrice"], "12.50")
        order.refresh_from_db()
        self.assertEqual(order.unit_price, Decimal("12.50"))
        commission = OrderCommission.objects.get(order=order)
        self.assertEqual(commission.reference_amount, Decimal("6250.00"))
        message = Message.objects.filter(buyer=self.buyer, supplier=self.supplier).last()
        self.assertIn("6 250,00 DA", message.content)
        self.assertIn("Prix négocié", message.content)

    def test_amount_is_locked_for_fixed_price_and_closed_orders(self):
        fixed = self._order(price=Decimal("10"))
        self.assertEqual(self._patch(fixed, {"unitPrice": "9"}).status_code, 400)
        closed = self._order(status=Order.Status.CANCELLED)
        self.assertEqual(self._patch(closed, {"unitPrice": "9"}).status_code, 400)
        open_order = self._order()
        self.assertEqual(self._patch(open_order, {"unitPrice": "0"}).status_code, 400)
        self.assertEqual(self._patch(open_order, {"unitPrice": "abc"}).status_code, 400)

    def test_other_supplier_cannot_edit_the_amount(self):
        order = self._order()
        other = make_supplier("Autre Co")
        other_client = APIClient()
        other_client.force_authenticate(make_supplier_user(other, email="autre@example.com"))
        self.assertEqual(self._patch(order, {"unitPrice": "9"}, other_client).status_code, 404)
        buyer_client = APIClient()
        buyer_client.force_authenticate(self.buyer)
        self.assertEqual(self._patch(order, {"unitPrice": "9"}, buyer_client).status_code, 403)


class SupplierCompetitorTests(TestCase):
    def setUp(self):
        self.parent = Category.objects.create(name_en="Films", level=1)
        self.sub_a = Category.objects.create(name_en="PE film", parent=self.parent, level=2)
        self.sub_b = Category.objects.create(name_en="PVC film", parent=self.parent, level=2)
        self.supplier_a = make_supplier("Alpha Pack")
        self.supplier_b = make_supplier("Beta Pack")
        self.supplier_c = make_supplier("Gamma Pack")
        Product.objects.create(
            supplier=self.supplier_a,
            name="PE roll",
            category=self.parent,
            subcategory=self.sub_a,
        )
        Product.objects.create(
            supplier=self.supplier_b,
            name="PE sheet",
            category=self.parent,
            subcategory=self.sub_a,
        )
        Product.objects.create(
            supplier=self.supplier_c,
            name="PVC roll",
            category=self.parent,
            subcategory=self.sub_b,
        )

    def test_shared_subcategory_defines_competitors(self):
        from user.supplier_competitors import (
            competitor_supplier_ids,
            count_direct_competitors,
            suppliers_share_subcategory,
        )

        self.assertTrue(suppliers_share_subcategory(self.supplier_a, self.supplier_b))
        self.assertFalse(suppliers_share_subcategory(self.supplier_a, self.supplier_c))
        self.assertEqual(
            set(competitor_supplier_ids(self.supplier_a)), {self.supplier_b.pk}
        )
        self.assertEqual(count_direct_competitors(self.supplier_a), 1)


class ProductExtraPhotoBillingTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        from user.pragmadinar import seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        self.supplier = make_supplier("Photo Co", plan="genereux")

    def test_fourth_photo_debits_point_one_pragmadinar(self):
        from decimal import Decimal

        from user.models import Category, Product
        from user.pragmadinar import credit_wallet, get_or_create_wallet
        from user.product_photos import bill_new_product_extra_photos

        credit_wallet(self.supplier, Decimal("5"))
        category = Category.objects.create(name_en="Pack", name_fr="Pack")
        product = Product.objects.create(
            supplier=self.supplier,
            name="Boite",
            category=category,
            description="Test",
        )
        bill_new_product_extra_photos(
            self.supplier, product, old_count=3, new_count=4
        )
        wallet = get_or_create_wallet(self.supplier)
        self.assertEqual(wallet.balance, Decimal("4.9"))


class VisibilityMeteringTests(TestCase):
    def setUp(self):
        entitlements.seed_entitlements()
        set_setting("visibility_metering_enabled", "true")
        self.supplier = make_supplier("Meter Co", plan="genereux")

    def test_search_appearance_increments_product_impressions(self):
        from django.test import RequestFactory

        from user.events import log_search_with_appearances

        request = RequestFactory().get("/")
        request.user = make_buyer("meter")
        log_search_with_appearances(
            request,
            search_query="carton",
            supplier_ids=[self.supplier.id],
            results_count=1,
            source="products",
        )
        used = entitlements.usage_for(
            self.supplier, entitlements.PRODUCT_IMPRESSIONS_MONTHLY
        )
        self.assertEqual(used, 1)

    def test_horizontal_layout_from_page_design_debits_pragmadinar(self):
        from decimal import Decimal

        from user.api.serializers import ensure_supplier_settings
        from user.campaigns import bill_product_card_store_visit
        from user.pragmadinar import credit_wallet, get_or_create_wallet, seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        credit_wallet(self.supplier, Decimal("20"))
        settings = ensure_supplier_settings(self.supplier)
        settings.product_card_style = {}
        settings.page_design = {"productCardLayout": "horizontal"}
        settings.save(update_fields=["product_card_style", "page_design"])

        before = get_or_create_wallet(self.supplier).balance
        for _ in range(10):
            bill_product_card_store_visit(self.supplier.id, request=None)
        wallet = get_or_create_wallet(self.supplier)
        self.assertEqual(wallet.balance, before)

    def test_horizontal_card_store_visit_debits_pragmadinar(self):
        from decimal import Decimal

        from django.test import RequestFactory

        from user.api.serializers import ensure_supplier_settings
        from user.campaigns import bill_product_card_store_visit
        from user.models import PragmadinarService
        from user.pragmadinar import credit_wallet, get_or_create_wallet, seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        svc = PragmadinarService.objects.get(code="visibility.card_horizontal")
        svc.cost_pragmadinar = Decimal("1.5")
        svc.save(update_fields=["cost_pragmadinar"])
        credit_wallet(self.supplier, Decimal("20"))
        settings = ensure_supplier_settings(self.supplier)
        settings.product_card_style = {"layout": "horizontal"}
        settings.save(update_fields=["product_card_style"])

        before = get_or_create_wallet(self.supplier).balance
        for _ in range(10):
            bill_product_card_store_visit(self.supplier.id, request=None)
        wallet = get_or_create_wallet(self.supplier)
        self.assertEqual(wallet.balance, before)

    def test_vitrine_extra_search_impression_debits_pragmadinar(self):
        from decimal import Decimal

        from django.test import RequestFactory

        from user.api.serializers import ensure_supplier_settings
        from user.events import log_search_with_appearances
        from user.pragmadinar import credit_wallet, get_or_create_wallet, seed_pragmadinar_catalog

        seed_pragmadinar_catalog()
        credit_wallet(self.supplier, Decimal("20"))
        settings = ensure_supplier_settings(self.supplier)
        settings.page_design = {
            "useStoreHeaderInSearch": True,
            "storeHeaderTier": "extra",
        }
        settings.save(update_fields=["page_design"])

        request = RequestFactory().get("/")
        request.user = make_buyer("search-meter")
        for i in range(5):
            log_search_with_appearances(
                request,
                search_query=f"sac-{i}",
                supplier_ids=[self.supplier.id],
                results_count=1,
                source="products",
            )
        wallet = get_or_create_wallet(self.supplier)
        self.assertEqual(wallet.balance, Decimal("20"))

    def test_business_still_records_usage_for_dashboards(self):
        from user.visibility_metering import meter_product_search_impression

        credit_wallet(self.supplier, 10)

        for _ in range(60):
            meter_product_search_impression(self.supplier.id)
        snapshot = entitlements.quota_snapshot(
            self.supplier, entitlements.PRODUCT_IMPRESSIONS_MONTHLY
        )
        self.assertTrue(snapshot["unlimited"])
        self.assertEqual(snapshot["used"], 60)
