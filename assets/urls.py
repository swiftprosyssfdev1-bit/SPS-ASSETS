from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("login/", views.BrandedLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="assets:login"), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("export/", views.export_assets, name="export_assets"),
    path("clear-all/", views.clear_all_assets, name="clear_all_assets"),
    path("import/", views.bulk_import, name="bulk_import"),
    path("import/confirm/", views.bulk_import_confirm, name="bulk_import_confirm"),
    path("import/sample/", views.bulk_import_sample, name="bulk_import_sample"),
    path("category/add/", views.category_create, name="category_create"),
    path("category/<int:category_id>/", views.category_detail, name="category_detail"),
    path("asset/add/", views.asset_create, name="asset_create"),
    path("asset/<int:asset_id>/edit/", views.asset_update, name="asset_update"),
    path("asset/<int:asset_id>/delete/", views.asset_delete, name="asset_delete"),
    path("search/", views.search_assets, name="search_assets"),
    path("search/suggestions/", views.search_suggestions, name="search_suggestions"),
    path("history/", views.history_list, name="history_list"),
]
