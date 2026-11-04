from django.contrib import admin

from buyer_space.models import (
    BuyerAccount,
    BuyerPragmadinarEntry,
    BuyerProduct,
    LibraryProduct,
    LibrarySector,
    PackagingItem,
    PackagingKit,
    ProductKitAssignment,
    ProductKitAudit,
    PackagingProject,
    PaksuitesComponent,
    PaksuitesOption,
    ProjectProductLink,
    ProjectProductVariety,
    ProjectSuiteKit,
    ServiceAvailability,
    ServiceRequest,
    ServiceRequestDeliverable,
    ServiceRequestEvent,
    ServiceRequestMessage,
)


class EntryInline(admin.TabularInline):
    model = BuyerPragmadinarEntry
    extra = 0
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BuyerAccount)
class BuyerAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at")
    search_fields = ("user__email",)
    inlines = [EntryInline]


@admin.register(BuyerPragmadinarEntry)
class BuyerPragmadinarEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "amount", "entry_type", "reason", "created_at")
    readonly_fields = (
        "account",
        "amount",
        "entry_type",
        "eligible_services",
        "consumed_entry",
        "service_request",
        "idempotency_key",
        "reason",
        "created_at",
    )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(PackagingProject)
admin.site.register(PackagingItem)
admin.site.register(BuyerProduct)
admin.site.register(LibrarySector)


class ProductKitAssignmentInline(admin.TabularInline):
    model = ProductKitAssignment
    extra = 0
    autocomplete_fields = ("kit",)


@admin.register(LibraryProduct)
class LibraryProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "sector", "is_active", "recommended_suite")
    list_filter = ("is_active", "sector")
    search_fields = ("code", "name")
    inlines = [ProductKitAssignmentInline]


@admin.register(ProductKitAssignment)
class ProductKitAssignmentAdmin(admin.ModelAdmin):
    list_display = ("product", "kit", "role", "updated_at")
    list_filter = ("role",)
    search_fields = ("product__code", "product__name", "kit__code", "kit__name")
    autocomplete_fields = ("product", "kit")


@admin.register(PackagingKit)
class PackagingKitAdmin(admin.ModelAdmin):
    list_display = ("ensemble", "status_label", "component_count")
    list_filter = ("status_label",)
    search_fields = ("ensemble__code", "ensemble__name", "components_label")


@admin.register(ProductKitAudit)
class ProductKitAuditAdmin(admin.ModelAdmin):
    list_display = ("product", "decision", "retained_principal_code")
    list_filter = ("decision",)
    search_fields = ("product__code", "product__name", "justification")


admin.site.register(ProjectProductLink)
admin.site.register(ProjectProductVariety)
admin.site.register(ProjectSuiteKit)
admin.site.register(ServiceAvailability)
admin.site.register(ServiceRequest)
admin.site.register(ServiceRequestMessage)
admin.site.register(ServiceRequestDeliverable)
admin.site.register(ServiceRequestEvent)
admin.site.register(PaksuitesOption)
admin.site.register(PaksuitesComponent)

from buyer_space import catalogue_admin  # noqa: E402,F401
