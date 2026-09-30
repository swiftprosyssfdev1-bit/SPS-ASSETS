from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("login/", views.BrandedLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="assets:login"), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("export/", views.export_assets, name="export_assets"),
    path("export-password/", views.export_password_settings, name="export_password"),
    path("account/", views.account_settings, name="account_settings"),
    path("deactivated/", views.deactivated_assets, name="deactivated_assets"),
    path("asset/<int:asset_id>/reactivate/", views.asset_reactivate, name="asset_reactivate"),
    path("clear-all/", views.clear_all_assets, name="clear_all_assets"),
    path("import/", views.bulk_import, name="bulk_import"),
    path("import/confirm/", views.bulk_import_confirm, name="bulk_import_confirm"),
    path("import/sample/", views.bulk_import_sample, name="bulk_import_sample"),
    path("category/add/", views.category_create, name="category_create"),
    path("category/<int:category_id>/edit/", views.category_edit, name="category_edit"),
    path("category/<int:category_id>/delete/", views.category_delete, name="category_delete"),
    path("category/<int:category_id>/", views.category_detail, name="category_detail"),
    # Workstation is a first-class module, not a normal Asset Category — it
    # gets its own stable URL instead of living under /category/<id>/ so it
    # never shows up as just another entry in the Asset Categories section.
    path("workstation/", views.workstation_list, name="workstation_list"),
    path("workstation/builder/", views.workstation_builder, name="workstation_builder"),
    path("workstation/builder/field/add/", views.workstation_field_add, name="workstation_field_add"),
    path("workstation/builder/field/<int:field_id>/edit/", views.workstation_field_edit, name="workstation_field_edit"),
    path("workstation/builder/field/<int:field_id>/deactivate/", views.workstation_field_deactivate, name="workstation_field_deactivate"),
    path("workstation/builder/field/<int:field_id>/reactivate/", views.workstation_field_reactivate, name="workstation_field_reactivate"),
    path("workstation/builder/field/<int:field_id>/delete/", views.workstation_field_delete, name="workstation_field_delete"),
    path("workstation/builder/field/reorder/", views.workstation_field_reorder, name="workstation_field_reorder"),
    path("workstation/rename/", views.workstation_rename, name="workstation_rename"),
    path("workstation/bulk-import/", views.workstation_bulk_import, name="workstation_bulk_import"),
    path("workstation/bulk-import/confirm/", views.workstation_bulk_import_confirm, name="workstation_bulk_import_confirm"),
    path("workstation/add/", views.workstation_asset_add, name="workstation_asset_add"),
    path("workstation/<int:asset_id>/edit/", views.workstation_asset_edit, name="workstation_asset_edit"),
    path("workstation/<int:asset_id>/view/", views.workstation_asset_view, name="workstation_asset_view"),
    path("asset/add/", views.asset_create, name="asset_create"),
    path("asset/<int:asset_id>/", views.asset_detail, name="asset_detail"),
    path("asset/<int:asset_id>/edit/", views.asset_update, name="asset_update"),
    path("asset/<int:asset_id>/delete/", views.asset_delete, name="asset_delete"),
    path("asset-lookup/", views.asset_lookup, name="asset_lookup"),
    path("search/", views.search_assets, name="search_assets"),
    path("search/suggestions/", views.search_suggestions, name="search_suggestions"),
    path("history/", views.history_list, name="history_list"),
    # Branch management (Super Admin only)
    path("branches/", views.branch_list, name="branch_list"),
    path("branches/add/", views.branch_create, name="branch_create"),
    path("branches/<int:branch_id>/edit/", views.branch_update, name="branch_update"),
    # Branch Admin management (Super Admin only)
    path("admins/", views.admin_list, name="admin_list"),
    path("admins/add/", views.admin_create, name="admin_create"),
    path("admins/<int:user_id>/edit/", views.admin_update, name="admin_update"),

    path("category/<int:category_id>/builder/", views.category_builder, name="category_builder"),
    path("category/<int:category_id>/builder/field/add/", views.builder_field_add, name="builder_field_add"),
    path("category/<int:category_id>/builder/field/<int:field_id>/edit/", views.builder_field_edit, name="builder_field_edit"),
    path("category/<int:category_id>/builder/field/<int:field_id>/deactivate/", views.builder_field_deactivate, name="builder_field_deactivate"),
    path("category/<int:category_id>/builder/field/<int:field_id>/reactivate/", views.builder_field_reactivate, name="builder_field_reactivate"),
    path("category/<int:category_id>/builder/field/<int:field_id>/delete/", views.builder_field_delete, name="builder_field_delete"),
    path("category/<int:category_id>/builder/field/reorder/", views.builder_field_reorder, name="builder_field_reorder"),
    path("config-audit/", views.config_audit_log, name="config_audit_log"),
    path("config-audit/clear/", views.config_audit_log_clear, name="config_audit_log_clear"),
]
