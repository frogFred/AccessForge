from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import StyledAuthenticationForm


app_name = "accessforge"

urlpatterns = [
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="registration/login_v2.html",
            authentication_form=StyledAuthenticationForm,
        ),
        name="login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path("", views.dashboard, name="dashboard"),
    path("tables/new/", views.table_create, name="table-create"),
    path("tables/<slug:slug>/", views.table_detail, name="table-detail"),
    path("tables/<slug:slug>/edit/", views.table_update, name="table-update"),
    path("tables/<slug:slug>/delete/", views.table_delete, name="table-delete"),
    path("tables/<slug:slug>/export/", views.export_csv, name="export-csv"),
    path("tables/<slug:slug>/report/", views.table_report, name="table-report"),
    path("tables/<slug:slug>/members/", views.table_members, name="table-members"),
    path("tables/<slug:slug>/members/<int:pk>/delete/", views.member_delete, name="member-delete"),
    path("tables/<slug:slug>/queries/", views.query_builder, name="query-builder"),
    path("tables/<slug:slug>/queries/<int:pk>/delete/", views.query_delete, name="query-delete"),
    path("tables/<slug:slug>/import/", views.import_data, name="import-data"),
    path("tables/<slug:slug>/form-designer/", views.form_designer, name="form-designer"),
    path("tables/<slug:slug>/fields/new/", views.field_create, name="field-create"),
    path("tables/<slug:slug>/fields/<int:pk>/edit/", views.field_update, name="field-update"),
    path("tables/<slug:slug>/fields/<int:pk>/delete/", views.field_delete, name="field-delete"),
    path("tables/<slug:slug>/records/new/", views.record_create, name="record-create"),
    path("tables/<slug:slug>/records/<int:pk>/edit/", views.record_update, name="record-update"),
    path("tables/<slug:slug>/records/<int:pk>/delete/", views.record_delete, name="record-delete"),
]
