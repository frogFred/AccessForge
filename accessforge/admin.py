from django.contrib import admin

from .models import (
    DataField,
    DataRecord,
    DataTable,
    FormLayout,
    FormLayoutField,
    SavedQuery,
    TableMembership,
)


class DataFieldInline(admin.TabularInline):
    model = DataField
    extra = 0
    fk_name = "table"


class TableMembershipInline(admin.TabularInline):
    model = TableMembership
    extra = 0


@admin.register(DataTable)
class DataTableAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "created_at", "updated_at")
    search_fields = ("name", "slug", "owner__username")
    inlines = [DataFieldInline, TableMembershipInline]


@admin.register(DataField)
class DataFieldAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "table",
        "field_type",
        "related_table",
        "view_role",
        "edit_role",
        "required",
        "order",
    )
    list_filter = ("field_type", "view_role", "edit_role", "required", "table", "related_table")
    search_fields = ("name", "slug", "table__name", "related_table__name")


@admin.register(DataRecord)
class DataRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "table", "updated_at", "created_at")
    list_filter = ("table",)
    search_fields = ("table__name",)


@admin.register(TableMembership)
class TableMembershipAdmin(admin.ModelAdmin):
    list_display = ("table", "user", "role", "updated_at")
    list_filter = ("role", "table")
    search_fields = ("table__name", "user__username", "user__email")


class FormLayoutFieldInline(admin.TabularInline):
    model = FormLayoutField
    extra = 0


@admin.register(FormLayout)
class FormLayoutAdmin(admin.ModelAdmin):
    list_display = ("table", "title", "columns", "updated_at")
    search_fields = ("table__name", "title")
    inlines = [FormLayoutFieldInline]


@admin.register(SavedQuery)
class SavedQueryAdmin(admin.ModelAdmin):
    list_display = ("name", "table", "created_by", "match_mode", "is_shared")
    list_filter = ("match_mode", "is_shared", "table")
    search_fields = ("name", "table__name", "created_by__username")
