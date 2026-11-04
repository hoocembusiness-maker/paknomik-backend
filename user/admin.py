"""
Registers Paknomik models in the Django admin site (/admin).
Staff can search, filter, and edit database rows from the browser.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    AppNotification,
    AttributeOption,
    AuditLog,
    Campaign,
    Category,
    CategoryAttribute,
    ConsentRecord,
    CrmActivity,
    CrmCompany,
    CrmContact,
    Industry,
    Lexicon,
    Material,
    Opportunity,
    Order,
    OrderCommission,
    PackagingComponent,
    PragmadinarLedger,
    PragmadinarRechargeRequest,
    PragmadinarService,
    PragmadinarStrategyReservation,
    PragmadinarWallet,
    PaknomikPlan,
    PlanEntitlement,
    PlatformRole,
    PlatformSetting,
    Product,
    ProductAttributeValue,
    ProductImage,
    ProductPackagingMapping,
    QuoteDemand,
    QuoteDemandEngagement,
    QuoteDemandSelection,
    QuoteFormField,
    QuoteFormTemplate,
    QuoteProposal,
    RFQRequestEvent,
    SupplierQuoteForm,
    RoleGrant,
    SearchTag,
    Supplier,
    SupplierMandate,
    TechnicalAttribute,
    Unit,
    UsageCounter,
    User,
    Event,
    Message,
    SupportConversation,
    SupportMessage,
    ContactMessage,
)
from .entitlements import seed_entitlements
from .pragmadinar import credit_wallet, seed_pragmadinar_catalog
from .platform_config import seed_settings
from .rbac import seed_roles


@admin.register(PlatformRole)
class PlatformRoleAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "is_system", "permission_count")
    search_fields = ("code", "label")
    actions = ["reseed_roles"]

    @admin.display(description="Permissions")
    def permission_count(self, obj):
        return len(obj.permissions or [])

    @admin.action(description="Recharger les rôles système")
    def reseed_roles(self, request, queryset):
        seed_roles()
        self.message_user(request, "Rôles système rechargés.")


@admin.register(RoleGrant)
class RoleGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "granted_by", "created_at", "revoked_at")
    list_filter = ("role", "revoked_at")
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user", "granted_by", "revoked_by")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "entity_type", "entity_id")
    list_filter = ("action", "entity_type", "created_at")
    search_fields = ("action", "entity_type", "actor__email", "reason")
    date_hierarchy = "created_at"
    # The audit trail is append-only.
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformSetting)
class PlatformSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "label", "updated_at")
    search_fields = ("key", "label")
    actions = ["reseed_settings"]

    @admin.action(description="Créer les réglages manquants")
    def reseed_settings(self, request, queryset):
        seed_settings()
        self.message_user(request, "Réglages par défaut vérifiés.")


@admin.register(PlanEntitlement)
class PlanEntitlementAdmin(admin.ModelAdmin):
    list_display = ("plan_code", "key", "label", "limit_display", "is_enabled")
    list_filter = ("plan_code", "is_enabled")
    search_fields = ("key", "label")
    list_editable = ("is_enabled",)
    actions = ["reseed_entitlements"]

    @admin.display(description="Limite")
    def limit_display(self, obj):
        return "illimité" if obj.limit_value is None else obj.limit_value

    @admin.action(description="Recharger les limites par défaut")
    def reseed_entitlements(self, request, queryset):
        seed_entitlements()
        self.message_user(request, "Limites par défaut rechargées.")


@admin.register(UsageCounter)
class UsageCounterAdmin(admin.ModelAdmin):
    list_display = ("supplier", "key", "year", "month", "used")
    list_filter = ("key", "year", "month")
    search_fields = ("supplier__company_name",)
    raw_id_fields = ("supplier",)


class CrmContactInline(admin.TabularInline):
    model = CrmContact
    extra = 0
    fields = ("first_name", "last_name", "job_title", "contact_consent")
    show_change_link = True


@admin.register(CrmCompany)
class CrmCompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "supplier", "status", "wilaya", "last_activity_at")
    list_filter = ("status", "is_archived")
    search_fields = ("name", "supplier__company_name")
    raw_id_fields = ("supplier", "buyer", "owner_user")
    inlines = [CrmContactInline]


@admin.register(CrmContact)
class CrmContactAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "company",
        "supplier",
        "preferred_channel",
        "contact_consent",
    )
    list_filter = ("preferred_channel", "contact_consent", "is_archived")
    search_fields = ("first_name", "last_name", "company__name")
    raw_id_fields = ("supplier", "company", "user")


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = ("title", "supplier", "stage", "source", "estimated_value", "updated_at")
    list_filter = ("stage", "source")
    search_fields = ("title", "supplier__company_name", "company__name")
    raw_id_fields = ("supplier", "company", "contact", "demand", "order")
    filter_horizontal = ("products",)


@admin.register(CrmActivity)
class CrmActivityAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "supplier", "due_at", "done_at", "created_at")
    list_filter = ("kind",)
    search_fields = ("title", "supplier__company_name")
    raw_id_fields = ("supplier", "opportunity", "company", "contact", "created_by")


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    list_display = ("buyer", "supplier", "purpose", "status", "requested_at", "responded_at")
    list_filter = ("purpose", "status")
    search_fields = ("buyer__email", "supplier__company_name")
    raw_id_fields = ("buyer", "supplier", "hold_ledger", "charge_ledger")
    readonly_fields = ("requested_at",)


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "supplier",
        "campaign_format",
        "status",
        "budget_pragmadinar",
        "spent_pragmadinar",
        "views_consumed",
        "view_cap",
    )
    list_filter = ("status", "campaign_format")
    search_fields = ("name", "supplier__company_name")
    raw_id_fields = ("supplier", "service")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "actor_role", "actor_user", "created_at")
    list_filter = ("event_type", "actor_role", "created_at")
    search_fields = ("event_type", "actor_user__email", "actor_user__username")
    raw_id_fields = ("actor_user",)
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "buyer", "sender", "created_at", "read_by_supplier", "read_by_buyer")
    list_filter = ("read_by_supplier", "read_by_buyer", "created_at")
    search_fields = ("content", "buyer__email", "supplier__company_name")
    raw_id_fields = ("supplier", "buyer", "sender")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


@admin.register(SupportConversation)
class SupportConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "subject", "status", "updated_at", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("subject", "user__email", "user__username")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "contact", "contact_type", "status", "user", "created_at")
    list_filter = ("status", "contact_type", "source", "created_at")
    search_fields = ("subject", "contact", "message")
    raw_id_fields = ("user",)
    readonly_fields = ("client_hash", "created_at")
    list_editable = ("status",)


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "is_staff_reply", "created_at", "read_at")
    list_filter = ("is_staff_reply", "created_at")
    search_fields = ("content", "sender__email")
    raw_id_fields = ("conversation", "sender")
    readonly_fields = ("created_at",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        'email',
        'username',
        'is_verified_client',
        'company_name',
        'is_active',
        'created_at',
    )
    list_filter = ('is_verified_client', 'is_active', 'is_staff')
    search_fields = ('email', 'username', 'company_name')
    ordering = ('-created_at',)
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            'Paknomik profile',
            {
                'fields': (
                    'is_verified_client',
                    'company_name',
                    'nature_of_activity',
                    'annual_purchase_volume',
                    'wilaya',
                    'address',
                    'phone_number',
                    'supplier',
                ),
            },
        ),
    )


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "sort_order",
        "level",
        "name_en",
        "name_fr",
        "parent",
        "slug",
        "is_new_in_v2",
        "external_id",
        "image",
    )
    list_filter = ("is_new_in_v2", "level")
    search_fields = ("name_en", "name_fr", "name_ar", "slug", "external_id")
    ordering = ("sort_order", "name_en")
    raw_id_fields = ("parent",)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'symbol')
    search_fields = ('name', 'symbol')


@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


class AttributeOptionInline(admin.TabularInline):
    model = AttributeOption
    extra = 0
    ordering = ("sort_order",)


class CategoryAttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 0
    raw_id_fields = ("category",)
    ordering = ("sort_order",)


@admin.register(TechnicalAttribute)
class TechnicalAttributeAdmin(admin.ModelAdmin):
    list_display = (
        "name_en",
        "attribute_level",
        "attribute_type",
        "unit",
        "is_required",
        "is_searchable",
        "is_comparable",
    )
    list_filter = (
        "attribute_level",
        "attribute_type",
        "is_required",
        "is_searchable",
        "is_comparable",
    )
    search_fields = ("name_en", "name_fr", "name_ar", "external_id")
    raw_id_fields = ("unit",)
    inlines = [CategoryAttributeInline]


@admin.register(CategoryAttribute)
class CategoryAttributeAdmin(admin.ModelAdmin):
    list_display = (
        "category",
        "attribute",
        "sort_order",
        "is_required_for_publish",
    )
    list_filter = ("is_required_for_publish", "attribute__attribute_level")
    search_fields = ("category__name_en", "attribute__name_en")
    raw_id_fields = ("category", "attribute")
    inlines = [AttributeOptionInline]


@admin.register(AttributeOption)
class AttributeOptionAdmin(admin.ModelAdmin):
    list_display = ("value_en", "category_attribute", "sort_order")
    search_fields = ("value_en", "category_attribute__category__name_en")
    raw_id_fields = ("category_attribute",)


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name_en', 'name_fr', 'family', 'external_id')
    list_filter = ('family',)
    search_fields = ('name_en', 'name_fr', 'name_ar', 'external_id')


@admin.register(Lexicon)
class LexiconAdmin(admin.ModelAdmin):
    list_display = ('keyword', 'french', 'arabic', 'search_weight')
    search_fields = ('keyword', 'french', 'arabic', 'synonyms')
    list_filter = ('search_weight',)


@admin.register(SearchTag)
class SearchTagAdmin(admin.ModelAdmin):
    list_display = ('tag', 'tag_fr', 'tag_ar', 'industry_name', 'search_weight')
    search_fields = ('tag', 'tag_fr', 'tag_ar', 'industry_name', 'synonyms')
    list_filter = ('tag_group', 'industry_name')


@admin.register(ProductPackagingMapping)
class ProductPackagingMappingAdmin(admin.ModelAdmin):
    list_display = ('final_product', 'industry', 'industry_name_raw')
    search_fields = ('final_product', 'industry_name_raw', 'primary_packaging')
    raw_id_fields = ('industry',)


@admin.register(PackagingComponent)
class PackagingComponentAdmin(admin.ModelAdmin):
    list_display = ('product_category', 'component_level', 'component_name', 'material')
    search_fields = ('product_category', 'component_name', 'material')


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        'company_name',
        'email',
        'wilaya',
        'paknomik_plan',
        'is_active',
        'is_featured',
        'is_ghost',
        'created_at',
        'logo',
    )
    list_filter = ('is_active', 'is_featured', 'is_ghost', 'wilaya', 'paknomik_plan')
    search_fields = ('company_name', 'email', 'address', 'presentation')
    filter_horizontal = ('interested_categories', 'interested_subcategories')
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'company_name',
                    'email',
                    'type',
                    'address',
                    'wilaya',
                    'website',
                    'whatsapp_number',
                    'presentation',
                    'presentation_html',
                    'logo',
                    'is_active',
                    'is_featured',
                    'is_ghost',
                    'paknomik_plan',
                    'brand_color',
                    'brand_neutral_color',
                ),
            },
        ),
        (
            'Catalog interests',
            {
                'fields': ('interested_categories', 'interested_subcategories'),
            },
        ),
    )


class ProductAttributeValueInline(admin.TabularInline):
    model = ProductAttributeValue
    extra = 0
    raw_id_fields = ('attribute',)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'supplier',
        'category',
        'subcategory',
        'material',
        'price',
        'is_active',
        'is_featured',
    )
    list_filter = ('is_active', 'is_featured', 'category', 'material', 'subcategory')
    search_fields = (
        'name',
        'name_ar',
        'supplier__company_name',
        'description',
        'search_tags__tag',
        'search_tags__tag_fr',
    )
    raw_id_fields = ('supplier', 'category', 'material', 'subcategory')
    filter_horizontal = ('search_tags',)
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'supplier',
                    'name',
                    'name_ar',
                    'description',
                    'description_html',
                ),
            },
        ),
        (
            'Catalog',
            {
                'fields': (
                    'category',
                    'subcategory',
                    'material',
                    'search_tags',
                ),
            },
        ),
        (
            'Pricing & availability',
            {
                'fields': (
                    'price',
                    'min_order_quantity',
                    'lead_time_days',
                ),
            },
        ),
        (
            'Status',
            {
                'fields': (
                    'is_active',
                    'is_featured',
                ),
            },
        ),
    )
    inlines = [ProductAttributeValueInline, ProductImageInline]


@admin.register(ProductAttributeValue)
class ProductAttributeValueAdmin(admin.ModelAdmin):
    list_display = ('product', 'attribute', 'num_value', 'txt_value', 'bool_value')
    search_fields = ('product__name', 'attribute__name_en')
    raw_id_fields = ('product', 'attribute')


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('product', 'is_primary', 'created_at')
    list_filter = ('is_primary',)
    raw_id_fields = ('product',)


@admin.register(PaknomikPlan)
class PaknomikPlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "monthly_price_dzd", "is_default")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id", "supplier", "buyer", "product", "quantity", "status", "origin",
        "trade_scope", "destination_country", "created_at",
    )
    list_filter = ("status", "origin", "trade_scope")
    search_fields = ("supplier__company_name", "buyer__email", "guest_name", "guest_phone")
    raw_id_fields = ("buyer", "supplier", "product", "quote_proposal", "quote_response")
    readonly_fields = ("commercial_snapshot", "created_at", "updated_at")


@admin.register(SupplierMandate)
class SupplierMandateAdmin(admin.ModelAdmin):
    list_display = (
        "supplier", "reference", "scope", "commission_rate", "status", "starts_on", "ends_on",
    )
    list_filter = ("status", "scope")
    search_fields = ("supplier__company_name", "reference")
    raw_id_fields = ("supplier", "created_by")


@admin.register(OrderCommission)
class OrderCommissionAdmin(admin.ModelAdmin):
    list_display = (
        "order", "trade_scope", "rate", "reference_amount", "amount", "currency",
        "status", "settlement_status", "created_at",
    )
    list_filter = ("status", "settlement_status", "trade_scope")
    raw_id_fields = ("order", "mandate", "validated_by")
    readonly_fields = ("rate", "created_at", "updated_at")


@admin.register(PragmadinarService)
class PragmadinarServiceAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "service_class",
        "wallet_category",
        "label",
        "price_rank",
        "cost_pragmadinar",
        "unit_label",
        "unit",
        "unit_quantity",
        "business_access",
        "genereux_access",
        "is_active",
    )
    list_filter = ("service_class", "wallet_category", "business_access", "genereux_access", "is_active")
    search_fields = ("code", "label")
    actions = ["reseed_catalog"]

    @admin.action(description="Recharger le listing tarifaire officiel")
    def reseed_catalog(self, request, queryset):
        seed_pragmadinar_catalog()
        self.message_user(request, "Listing tarifaire synchronisé.")


@admin.register(PragmadinarWallet)
class PragmadinarWalletAdmin(admin.ModelAdmin):
    list_display = ("supplier", "category", "balance", "held_total", "updated_at")
    search_fields = ("supplier__company_name",)


@admin.register(PragmadinarStrategyReservation)
class PragmadinarStrategyReservationAdmin(admin.ModelAdmin):
    list_display = (
        "supplier",
        "wallet",
        "service",
        "target_quantity",
        "reserved_amount",
        "status",
        "ends_at",
    )
    list_filter = ("status", "period", "wallet__category")
    search_fields = ("supplier__company_name", "service__label")


class PragmadinarLedgerInline(admin.TabularInline):
    model = PragmadinarLedger
    extra = 0
    readonly_fields = (
        "service",
        "amount",
        "reason",
        "ref_type",
        "ref_id",
        "refundable",
        "created_at",
    )


@admin.register(PragmadinarLedger)
class PragmadinarLedgerAdmin(admin.ModelAdmin):
    list_display = ("wallet", "amount", "reason", "refundable", "created_at")
    list_filter = ("refundable",)
    search_fields = ("wallet__supplier__company_name", "reason")


@admin.register(PragmadinarRechargeRequest)
class PragmadinarRechargeRequestAdmin(admin.ModelAdmin):
    list_display = ("supplier", "amount", "status", "created_at")
    list_filter = ("status",)
    actions = ["approve_recharges"]

    @admin.action(description="Approuver et créditer le wallet")
    def approve_recharges(self, request, queryset):
        from django.utils import timezone

        count = 0
        for row in queryset.filter(status=PragmadinarRechargeRequest.Status.PENDING):
            credit_wallet(
                row.supplier,
                row.amount,
                reason="Recharge pragmadinar",
                ref_type="recharge_request",
                ref_id=row.id,
            )
            row.status = PragmadinarRechargeRequest.Status.APPROVED
            row.reviewed_at = timezone.now()
            row.save(update_fields=["status", "reviewed_at"])
            count += 1
        self.message_user(request, f"{count} recharge(s) approuvée(s).")


class QuoteDemandSelectionInline(admin.TabularInline):
    model = QuoteDemandSelection
    extra = 0


class QuoteDemandEngagementInline(admin.TabularInline):
    model = QuoteDemandEngagement
    extra = 0


class QuoteProposalInline(admin.TabularInline):
    model = QuoteProposal
    extra = 0


@admin.register(QuoteDemand)
class QuoteDemandAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "kind",
        "status",
        "visible_on_market",
        "admin_queue",
        "buyer",
        "subcategory",
        "quantity",
        "created_at",
    )
    list_filter = ("kind", "status", "visible_on_market", "admin_queue")
    raw_id_fields = ("buyer", "category", "subcategory")
    inlines = [QuoteDemandSelectionInline, QuoteDemandEngagementInline, QuoteProposalInline]


@admin.register(QuoteProposal)
class QuoteProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "demand", "supplier", "unit_price", "status", "created_at")
    list_filter = ("status",)


@admin.register(AppNotification)
class AppNotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "is_read", "created_at")
    list_filter = ("kind", "is_read")


class QuoteFormFieldInline(admin.TabularInline):
    model = QuoteFormField
    extra = 0


@admin.register(QuoteFormTemplate)
class QuoteFormTemplateAdmin(admin.ModelAdmin):
    list_display = ("title_fr", "category", "quantity_unit", "version")
    inlines = [QuoteFormFieldInline]


@admin.register(SupplierQuoteForm)
class SupplierQuoteFormAdmin(admin.ModelAdmin):
    list_display = ("supplier", "template", "physical_customization", "updated_at")
    raw_id_fields = ("supplier", "template")


@admin.register(RFQRequestEvent)
class RFQRequestEventAdmin(admin.ModelAdmin):
    list_display = ("rfq", "action", "actor", "created_at")
    raw_id_fields = ("rfq", "actor")
