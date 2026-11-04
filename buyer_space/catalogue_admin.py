from django.contrib import admin

from buyer_space.catalogue_models import (
    SuiteAttribute,
    SuiteCompatibility,
    SuiteComponentDefinition,
    SuiteComponentLine,
    SuiteEnsemble,
    SuiteFavorite,
    SuiteImage,
    SuiteReference,
    SuiteSource,
    SuiteSupplierOffer,
    SuiteTaxon,
    SuiteVariant,
    publish_blockers,
)


class LineInline(admin.TabularInline):
    model = SuiteComponentLine
    extra = 0


class ImageInline(admin.TabularInline):
    model = SuiteImage
    extra = 0
    fk_name = "ensemble"
    fields = (
        "view_type",
        "file",
        "suggested_filename",
        "production_status",
        "rights",
        "illustrative",
        "generation_prompt",
    )
    readonly_fields = ("suggested_filename",)


@admin.register(SuiteEnsemble)
class SuiteEnsembleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "priority", "confidence", "editorial_status", "updated_at")
    list_filter = ("editorial_status", "confidence", "priority")
    search_fields = ("code", "name", "target_product")
    filter_horizontal = ("sectors", "sources")
    inlines = [LineInline, ImageInline]
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if obj.editorial_status == SuiteEnsemble.Status.PUBLISHED:
            blockers = publish_blockers(obj) if obj.pk else ["Enregistrez les composants avant de publier."]
            if blockers:
                obj.editorial_status = SuiteEnsemble.Status.REVIEW
                self.message_user(request, "Publication refusée : " + " ".join(blockers))
        super().save_model(request, obj, form, change)


@admin.register(SuiteTaxon)
class SuiteTaxonAdmin(admin.ModelAdmin):
    list_display = ("kind", "code", "label_fr", "parent")
    list_filter = ("kind",)
    search_fields = ("code", "label_fr")


admin.site.register(SuiteSource)
admin.site.register(SuiteComponentDefinition)
admin.site.register(SuiteAttribute)
admin.site.register(SuiteReference)
admin.site.register(SuiteSupplierOffer)
admin.site.register(SuiteCompatibility)
admin.site.register(SuiteVariant)
admin.site.register(SuiteFavorite)
