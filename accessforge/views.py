from __future__ import annotations

import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from .forms import (
    DynamicRecordForm,
    FieldForm,
    ImportDataForm,
    LayoutForm,
    LayoutItemFormSet,
    MembershipAssignmentForm,
    QueryBuilderForm,
    QueryConditionFormSet,
    TableForm,
)
from .models import (
    ROLE_EDITOR,
    ROLE_OWNER,
    DataField,
    DataRecord,
    DataTable,
    FormLayoutField,
    SavedQuery,
    TableMembership,
)
from .permissions import require_table_editor, require_table_owner, require_table_role
from .services import import_rows_to_table, parse_uploaded_rows, run_query


def _accessible_tables(user):
    queryset = DataTable.objects.annotate(
        field_count=Count("fields", distinct=True),
        record_count=Count("records", distinct=True),
    ).order_by("name")
    if user.is_superuser:
        return queryset
    return queryset.filter(Q(owner=user) | Q(memberships__user=user)).distinct()


def _table_for_user(user, slug):
    table = get_object_or_404(
        DataTable.objects.select_related("owner").prefetch_related("fields", "memberships__user"),
        slug=slug,
    )
    require_table_role(table, user)
    return table


def _build_record_rows(table: DataTable, records):
    fields = list(table.ordered_fields)
    rows = []
    for record in records:
        rows.append(
            {
                "record": record,
                "cells": [field.display_value(record.data.get(field.slug)) for field in fields],
            }
        )
    return fields, rows


@login_required
def dashboard(request):
    tables = _accessible_tables(request.user)
    recent_records = (
        DataRecord.objects.select_related("table")
        .filter(table__in=tables)
        .order_by("-updated_at", "-id")[:10]
    )
    return render(
        request,
        "accessforge/dashboard_v2.html",
        {
            "tables": tables,
            "recent_records": recent_records,
        },
    )


@login_required
def table_create(request):
    form = TableForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        table = form.save(commit=False)
        table.owner = request.user
        table.save()
        messages.success(request, "Table created.")
        return redirect(table)
    return render(
        request,
        "accessforge/table_form_v2.html",
        {
            "form": form,
            "title": "Create table",
            "submit_label": "Create table",
        },
    )


@login_required
def table_update(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    form = TableForm(request.POST or None, instance=table)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Table settings updated.")
        return redirect(table)
    return render(
        request,
        "accessforge/table_form_v2.html",
        {
            "form": form,
            "table": table,
            "title": f"Edit table: {table.name}",
            "submit_label": "Save table",
        },
    )


@login_required
def table_delete(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    if request.method == "POST":
        table.delete()
        messages.success(request, "Table deleted.")
        return redirect("accessforge:dashboard")
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"Delete table: {table.name}",
            "message": "This removes the table, fields, records, queries, and form layout.",
            "cancel_url": table.get_absolute_url(),
        },
    )


@login_required
def table_detail(request, slug):
    table = _table_for_user(request.user, slug)
    records = table.records.order_by("-updated_at", "-id")
    search_query = request.GET.get("q", "").strip().lower()
    if search_query:
        filtered = []
        for record in records:
            haystack = " ".join(
                field.display_value(record.data.get(field.slug))
                for field in table.ordered_fields
            ).lower()
            if search_query in haystack:
                filtered.append(record)
        records = filtered
    fields, record_rows = _build_record_rows(table, records)
    outgoing_relations = table.fields.filter(field_type=DataField.RELATION).select_related("related_table")
    incoming_relations = DataField.objects.filter(
        field_type=DataField.RELATION,
        related_table=table,
    ).select_related("table")
    memberships = table.memberships.select_related("user").order_by("user__username")
    saved_queries = table.saved_queries.order_by("name")
    return render(
        request,
        "accessforge/table_detail_v2.html",
        {
            "table": table,
            "fields": fields,
            "record_rows": record_rows,
            "search_query": request.GET.get("q", "").strip(),
            "outgoing_relations": outgoing_relations,
            "incoming_relations": incoming_relations,
            "memberships": memberships,
            "saved_queries": saved_queries,
            "can_edit": table.has_role(request.user, ROLE_EDITOR),
            "can_manage": table.has_role(request.user, ROLE_OWNER),
        },
    )


@login_required
def field_create(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    form = FieldForm(request.POST or None, table=table)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Field added.")
        return redirect(table)
    return render(
        request,
        "accessforge/field_form_v2.html",
        {
            "form": form,
            "table": table,
            "title": f"Add field to {table.name}",
            "submit_label": "Create field",
        },
    )


@login_required
def field_update(request, slug, pk):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    field = get_object_or_404(DataField, pk=pk, table=table)
    form = FieldForm(request.POST or None, table=table, instance=field)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Field updated.")
        return redirect(table)
    return render(
        request,
        "accessforge/field_form_v2.html",
        {
            "form": form,
            "table": table,
            "field": field,
            "title": f"Edit field: {field.name}",
            "submit_label": "Save field",
        },
    )


@login_required
def field_delete(request, slug, pk):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    field = get_object_or_404(DataField, pk=pk, table=table)
    if request.method == "POST":
        field.delete()
        messages.success(request, "Field deleted.")
        return redirect(table)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"Delete field: {field.name}",
            "message": "This also removes the column data from all existing records.",
            "cancel_url": table.get_absolute_url(),
        },
    )


@login_required
def record_create(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_editor(table, request.user)
    if not table.fields.exists():
        messages.warning(request, "Create at least one field before adding records.")
        return redirect(table)
    layout = table.get_or_create_layout()
    form = DynamicRecordForm(request.POST or None, table=table, layout=layout)
    if request.method == "POST" and form.is_valid():
        record = form.save()
        messages.success(request, f"Record #{record.pk} created.")
        return redirect(table)
    return render(
        request,
        "accessforge/record_form_v2.html",
        {
            "form": form,
            "layout": layout,
            "layout_sections": form.get_layout_sections(),
            "table": table,
            "title": f"New {table.name} record",
            "submit_label": layout.submit_label,
        },
    )


@login_required
def record_update(request, slug, pk):
    table = _table_for_user(request.user, slug)
    require_table_editor(table, request.user)
    record = get_object_or_404(DataRecord, pk=pk, table=table)
    layout = table.get_or_create_layout()
    form = DynamicRecordForm(
        request.POST or None,
        table=table,
        instance=record,
        layout=layout,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Record #{record.pk} updated.")
        return redirect(table)
    return render(
        request,
        "accessforge/record_form_v2.html",
        {
            "form": form,
            "layout": layout,
            "layout_sections": form.get_layout_sections(),
            "table": table,
            "record": record,
            "title": f"Edit {table.name} record #{record.pk}",
            "submit_label": layout.submit_label,
        },
    )


@login_required
def record_delete(request, slug, pk):
    table = _table_for_user(request.user, slug)
    require_table_editor(table, request.user)
    record = get_object_or_404(DataRecord, pk=pk, table=table)
    if request.method == "POST":
        record.delete()
        messages.success(request, "Record deleted.")
        return redirect(table)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"Delete record #{record.pk}",
            "message": "This record will be permanently removed.",
            "cancel_url": table.get_absolute_url(),
        },
    )


@login_required
def table_members(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    form = MembershipAssignmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        membership = form.save(table)
        messages.success(
            request,
            f"Role '{membership.role}' assigned to {membership.user.username}.",
        )
        return redirect("accessforge:table-members", slug=table.slug)
    memberships = table.memberships.select_related("user").order_by("user__username")
    return render(
        request,
        "accessforge/table_members_v2.html",
        {
            "table": table,
            "form": form,
            "memberships": memberships,
        },
    )


@login_required
def member_delete(request, slug, pk):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    membership = get_object_or_404(TableMembership, pk=pk, table=table)
    if membership.user_id == table.owner_id:
        messages.error(request, "The table owner cannot be removed from memberships.")
        return redirect("accessforge:table-members", slug=table.slug)
    if request.method == "POST":
        membership.delete()
        messages.success(request, "Member removed.")
        return redirect("accessforge:table-members", slug=table.slug)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"Remove {membership.user.username}",
            "message": "This user will lose access to the table.",
            "cancel_url": reverse("accessforge:table-members", kwargs={"slug": table.slug}),
        },
    )


@login_required
def query_builder(request, slug):
    table = _table_for_user(request.user, slug)
    saved_query = None
    query_id = request.GET.get("saved")
    if query_id:
        saved_query = get_object_or_404(SavedQuery, pk=query_id, table=table)

    if request.method == "POST":
        query_instance = None
        if request.POST.get("query_id"):
            query_instance = get_object_or_404(
                SavedQuery,
                pk=request.POST["query_id"],
                table=table,
            )
        query_form = QueryBuilderForm(
            request.POST,
            instance=query_instance,
            prefix="query",
            initial={"save_query": bool(query_instance)},
        )
        condition_formset = QueryConditionFormSet(
            request.POST,
            table=table,
            prefix="conditions",
        )
        results = []
        if query_form.is_valid() and condition_formset.is_valid():
            conditions = []
            for form in condition_formset.forms:
                if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                    continue
                if not any(form.cleaned_data.get(key) for key in ("field", "operator", "value")):
                    continue
                conditions.append(
                    {
                        "field": form.cleaned_data["field"],
                        "operator": form.cleaned_data["operator"],
                        "value": form.cleaned_data.get("value", ""),
                    }
                )
            try:
                results = run_query(
                    table.records.order_by("-updated_at", "-id"),
                    table,
                    conditions,
                    query_form.cleaned_data["match_mode"],
                )
            except ValidationError as exc:
                condition_formset._non_form_errors = condition_formset.error_class(exc.messages)
            else:
                if query_form.cleaned_data["save_query"]:
                    saved = query_form.save(commit=False)
                    saved.table = table
                    saved.created_by = request.user
                    saved.conditions = conditions
                    saved.save()
                    messages.success(request, f"Saved query '{saved.name}' updated.")
                    return redirect(f"{reverse('accessforge:query-builder', kwargs={'slug': table.slug})}?saved={saved.pk}")
        fields, record_rows = _build_record_rows(table, results)
    else:
        if saved_query:
            query_form = QueryBuilderForm(
                instance=saved_query,
                prefix="query",
                initial={"save_query": True},
            )
            condition_formset = QueryConditionFormSet(
                table=table,
                prefix="conditions",
                initial=saved_query.conditions,
            )
            results = run_query(
                table.records.order_by("-updated_at", "-id"),
                table,
                saved_query.conditions,
                saved_query.match_mode,
            )
        else:
            query_form = QueryBuilderForm(
                prefix="query",
                initial={"match_mode": SavedQuery.MATCH_ALL, "is_shared": True},
            )
            condition_formset = QueryConditionFormSet(table=table, prefix="conditions")
            results = []
        fields, record_rows = _build_record_rows(table, results)

    return render(
        request,
        "accessforge/query_builder_v2.html",
        {
            "table": table,
            "query_form": query_form,
            "condition_formset": condition_formset,
            "fields": fields,
            "record_rows": record_rows,
            "saved_query": saved_query,
            "saved_queries": table.saved_queries.order_by("name"),
        },
    )


@login_required
def query_delete(request, slug, pk):
    table = _table_for_user(request.user, slug)
    query = get_object_or_404(SavedQuery, pk=pk, table=table)
    if not (table.has_role(request.user, ROLE_OWNER) or query.created_by_id == request.user.id):
        require_table_owner(table, request.user)
    if request.method == "POST":
        query.delete()
        messages.success(request, "Saved query deleted.")
        return redirect("accessforge:query-builder", slug=table.slug)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"Delete query: {query.name}",
            "message": "This saved query will be removed.",
            "cancel_url": reverse("accessforge:query-builder", kwargs={"slug": table.slug}),
        },
    )


@login_required
def import_data(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_editor(table, request.user)
    form = ImportDataForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            headers, rows = parse_uploaded_rows(form.cleaned_data["source_file"])
            summary = import_rows_to_table(
                table,
                headers,
                rows,
                create_missing_fields=form.cleaned_data["create_missing_fields"],
            )
        except ValidationError as exc:
            form.add_error("source_file", "; ".join(exc.messages))
        else:
            messages.success(
                request,
                f"Imported {summary['created_records']} records and created {summary['created_fields']} fields.",
            )
            return redirect(table)
    return render(
        request,
        "accessforge/import_data_v2.html",
        {
            "table": table,
            "form": form,
        },
    )


@login_required
def form_designer(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    layout = table.get_or_create_layout()
    layout_form = LayoutForm(request.POST or None, instance=layout, prefix="layout")
    item_queryset = layout.ordered_items
    item_formset = LayoutItemFormSet(
        request.POST or None,
        queryset=item_queryset,
        prefix="items",
    )
    if request.method == "POST" and layout_form.is_valid() and item_formset.is_valid():
        layout = layout_form.save()
        instances = item_formset.save(commit=False)
        updated_ids = set()
        for instance in instances:
            instance.layout = layout
            instance.column_span = min(max(instance.column_span, 1), max(layout.columns, 1))
            instance.save()
            updated_ids.add(instance.pk)
        untouched = FormLayoutField.objects.filter(layout=layout).exclude(pk__in=updated_ids)
        for item in untouched:
            item.column_span = min(max(item.column_span, 1), max(layout.columns, 1))
            item.save(update_fields=["column_span", "updated_at"])
        messages.success(request, "Form layout updated.")
        return redirect("accessforge:form-designer", slug=table.slug)
    return render(
        request,
        "accessforge/form_designer_v2.html",
        {
            "table": table,
            "layout": layout,
            "layout_form": layout_form,
            "item_formset": item_formset,
        },
    )


@login_required
def export_csv(request, slug):
    table = _table_for_user(request.user, slug)
    fields = list(table.ordered_fields)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = slugify(table.name) or table.slug
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    writer = csv.writer(response)
    writer.writerow([field.name for field in fields])
    for record in table.records.order_by("id"):
        writer.writerow([field.display_value(record.data.get(field.slug)) for field in fields])
    return response
