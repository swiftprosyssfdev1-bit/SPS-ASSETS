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
    path("category/<int:category_id>/", views.category_detail, name="category_detail"),
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
]
