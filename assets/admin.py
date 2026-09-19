from django.contrib import admin
from .models import AssetCategory, Asset, AssetHistory


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "asset_count", "show_under_other")
    list_editable = ("show_under_other",)
    search_fields = ("name",)

    def asset_count(self, obj):
        return obj.assets.count()
    asset_count.short_description = "Assets"


class AssetHistoryInline(admin.TabularInline):
    model = AssetHistory
    extra = 0
    readonly_fields = ("field_name", "old_value", "new_value", "changed_by", "changed_at")
    can_delete = False
    ordering = ("-changed_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "asset_tag", "name", "category", "status",
        "current_assigned_to", "current_location", "is_active", "updated_at",
    )
    list_filter = ("category", "status", "is_active")
    search_fields = (
        "asset_tag", "name", "brand", "model_number", "serial_number",
        "current_assigned_to", "current_location", "linked_workstation",
    )
    autocomplete_fields = ("category",)
    inlines = [AssetHistoryInline]
    fieldsets = (
        ("Identification", {
            "fields": ("category", "asset_tag", "name", "brand", "model_number", "serial_number")
        }),
        ("Current status", {
            "fields": ("status", "current_assigned_to", "current_location", "linked_workstation", "is_active")
        }),
        ("Previous (auto-filled on change)", {
            "fields": ("previous_assigned_to", "previous_location"),
            "classes": ("collapse",),
        }),
        ("Dates", {
            "fields": ("purchase_date", "last_service_date")
        }),
        ("Extra details", {
            "fields": ("extra_details", "notes"),
            "description": "Use extra_details for anything category-specific, e.g. "
                            '{"product_key": "XXXX-XXXX", "os": "Win 10 Pro", "employee_id": "TR1638"}',
        }),
    )
    readonly_fields = ("previous_assigned_to", "previous_location")

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(AssetHistory)
class AssetHistoryAdmin(admin.ModelAdmin):
    list_display = ("asset", "field_name", "old_value", "new_value", "changed_by", "changed_at")
    list_filter = ("field_name",)
    search_fields = ("asset__asset_tag", "asset__name")
    readonly_fields = [f.name for f in AssetHistory._meta.fields]

    def has_add_permission(self, request):
        return False
