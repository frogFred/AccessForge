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
    ReportBuilderForm,
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
from .services import (
    FieldPathSpec,
    display_value_for_field_path,
    get_accessible_field_paths,
    get_visible_fields,
    import_rows_to_table,
    parse_uploaded_rows,
    resolve_field_path,
    run_query,
)


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


def _visible_field_specs(table: DataTable, user):
    return [
        FieldPathSpec(token=field.slug, label=field.name, field=field)
        for field in get_visible_fields(table, user)
    ]


def _build_record_rows(records, field_specs):
    rows = []
    for record in records:
        rows.append(
            {
                "record": record,
                "cells": [display_value_for_field_path(record, field_spec) for field_spec in field_specs],
            }
        )
    return rows


def _available_queries(table: DataTable, user):
    return table.saved_queries.filter(Q(is_shared=True) | Q(created_by=user)).distinct().order_by("name")


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
        messages.success(request, "資料表已建立。")
        return redirect(table)
    return render(
        request,
        "accessforge/table_form_v2.html",
        {
            "form": form,
            "title": "建立資料表",
            "submit_label": "建立資料表",
        },
    )


@login_required
def table_update(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    form = TableForm(request.POST or None, instance=table)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "資料表設定已更新。")
        return redirect(table)
    return render(
        request,
        "accessforge/table_form_v2.html",
        {
            "form": form,
            "table": table,
            "title": f"編輯資料表：{table.name}",
            "submit_label": "儲存資料表",
        },
    )


@login_required
def table_delete(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    if request.method == "POST":
        table.delete()
        messages.success(request, "資料表已刪除。")
        return redirect("accessforge:dashboard")
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"刪除資料表：{table.name}",
            "message": "這會一併刪除資料表、欄位、資料、查詢與表單版型。",
            "cancel_url": table.get_absolute_url(),
        },
    )


@login_required
def table_detail(request, slug):
    table = _table_for_user(request.user, slug)
    records = table.records.order_by("-updated_at", "-id")
    visible_field_specs = _visible_field_specs(table, request.user)
    search_query = request.GET.get("q", "").strip().lower()
    if search_query:
        filtered = []
        for record in records:
            haystack = " ".join(
                display_value_for_field_path(record, field_spec)
                for field_spec in visible_field_specs
            ).lower()
            if search_query in haystack:
                filtered.append(record)
        records = filtered
    record_rows = _build_record_rows(records, visible_field_specs)
    outgoing_relations = [
        field
        for field in table.fields.filter(field_type=DataField.RELATION).select_related("related_table")
        if table.has_role(request.user, ROLE_OWNER) or field.can_view(request.user)
    ]
    incoming_relations = [
        field
        for field in DataField.objects.filter(
        field_type=DataField.RELATION,
        related_table=table,
    ).select_related("table")
        if field.table.has_role(request.user) and field.can_view(request.user)
    ]
    memberships = table.memberships.select_related("user").order_by("user__username")
    saved_queries = _available_queries(table, request.user)
    return render(
        request,
        "accessforge/table_detail_v3.html",
        {
            "table": table,
            "fields": visible_field_specs,
            "schema_fields": list(table.ordered_fields) if table.has_role(request.user, ROLE_OWNER) else get_visible_fields(table, request.user),
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
        messages.success(request, "欄位已新增。")
        return redirect(table)
    return render(
        request,
        "accessforge/field_form_v2.html",
        {
            "form": form,
            "table": table,
            "title": f"新增欄位到 {table.name}",
            "submit_label": "建立欄位",
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
        messages.success(request, "欄位已更新。")
        return redirect(table)
    return render(
        request,
        "accessforge/field_form_v2.html",
        {
            "form": form,
            "table": table,
            "field": field,
            "title": f"編輯欄位：{field.name}",
            "submit_label": "儲存欄位",
        },
    )


@login_required
def field_delete(request, slug, pk):
    table = _table_for_user(request.user, slug)
    require_table_owner(table, request.user)
    field = get_object_or_404(DataField, pk=pk, table=table)
    if request.method == "POST":
        field.delete()
        messages.success(request, "欄位已刪除。")
        return redirect(table)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"刪除欄位：{field.name}",
            "message": "這也會移除所有既有記錄中的這個欄位資料。",
            "cancel_url": table.get_absolute_url(),
        },
    )


@login_required
def record_create(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_editor(table, request.user)
    if not table.fields.exists():
        messages.warning(request, "請先建立至少一個欄位，再新增資料。")
        return redirect(table)
    layout = table.get_or_create_layout()
    form = DynamicRecordForm(request.POST or None, table=table, user=request.user, layout=layout)
    if request.method == "POST" and form.is_valid():
        record = form.save()
        messages.success(request, f"資料 #{record.pk} 已建立。")
        return redirect(table)
    return render(
        request,
        "accessforge/record_form_v2.html",
        {
            "form": form,
            "layout": layout,
            "layout_sections": form.get_layout_sections(),
            "table": table,
            "can_manage": table.has_role(request.user, ROLE_OWNER),
            "title": f"新增 {table.name} 資料",
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
        user=request.user,
        instance=record,
        layout=layout,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"資料 #{record.pk} 已更新。")
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
            "can_manage": table.has_role(request.user, ROLE_OWNER),
            "title": f"編輯 {table.name} 資料 #{record.pk}",
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
        messages.success(request, "資料已刪除。")
        return redirect(table)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"刪除資料 #{record.pk}",
            "message": "這筆資料會被永久刪除。",
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
            f"已將 {membership.user.username} 設定為「{membership.get_role_display()}」。",
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
        messages.error(request, "資料表擁有者不能從成員名單中移除。")
        return redirect("accessforge:table-members", slug=table.slug)
    if request.method == "POST":
        membership.delete()
        messages.success(request, "成員已移除。")
        return redirect("accessforge:table-members", slug=table.slug)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"移除成員：{membership.user.username}",
            "message": "這位使用者將失去這張資料表的存取權限。",
            "cancel_url": reverse("accessforge:table-members", kwargs={"slug": table.slug}),
        },
    )


@login_required
def query_builder(request, slug):
    table = _table_for_user(request.user, slug)
    saved_query = None
    query_id = request.GET.get("saved")
    if query_id:
        saved_query = get_object_or_404(_available_queries(table, request.user), pk=query_id, table=table)

    if request.method == "POST":
        query_instance = None
        if request.POST.get("query_id"):
            query_instance = get_object_or_404(
                _available_queries(table, request.user),
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
            user=request.user,
            prefix="conditions",
        )
        results = []
        if query_form.is_valid() and condition_formset.is_valid():
            conditions = []
            for form in condition_formset.forms:
                if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                    continue
                if not any(form.cleaned_data.get(key) for key in ("field_path", "operator", "value")):
                    continue
                conditions.append(
                    {
                        "field_path": form.cleaned_data["field_path"],
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
                    user=request.user,
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
                    messages.success(request, f"已儲存查詢「{saved.name}」。")
                    return redirect(f"{reverse('accessforge:query-builder', kwargs={'slug': table.slug})}?saved={saved.pk}")
        field_specs = _visible_field_specs(table, request.user)
        record_rows = _build_record_rows(results, field_specs)
    else:
        if saved_query:
            query_form = QueryBuilderForm(
                instance=saved_query,
                prefix="query",
                initial={"save_query": True},
            )
            condition_formset = QueryConditionFormSet(
                table=table,
                user=request.user,
                prefix="conditions",
                initial=saved_query.conditions,
            )
            try:
                results = run_query(
                    table.records.order_by("-updated_at", "-id"),
                    table,
                    saved_query.conditions,
                    saved_query.match_mode,
                    user=request.user,
                )
            except ValidationError as exc:
                condition_formset._non_form_errors = condition_formset.error_class(exc.messages)
                results = []
        else:
            query_form = QueryBuilderForm(
                prefix="query",
                initial={"match_mode": SavedQuery.MATCH_ALL, "is_shared": True},
            )
            condition_formset = QueryConditionFormSet(table=table, user=request.user, prefix="conditions")
            results = []
        field_specs = _visible_field_specs(table, request.user)
        record_rows = _build_record_rows(results, field_specs)

    return render(
        request,
        "accessforge/query_builder_v3.html",
        {
            "table": table,
            "query_form": query_form,
            "condition_formset": condition_formset,
            "fields": field_specs,
            "record_rows": record_rows,
            "saved_query": saved_query,
            "saved_queries": _available_queries(table, request.user),
            "join_paths": [spec for spec in get_accessible_field_paths(table, request.user, include_joined=True) if spec.is_joined],
        },
    )


@login_required
def query_delete(request, slug, pk):
    table = _table_for_user(request.user, slug)
    query = get_object_or_404(_available_queries(table, request.user), pk=pk, table=table)
    if not (table.has_role(request.user, ROLE_OWNER) or query.created_by_id == request.user.id):
        require_table_owner(table, request.user)
    if request.method == "POST":
        query.delete()
        messages.success(request, "已刪除儲存查詢。")
        return redirect("accessforge:query-builder", slug=table.slug)
    return render(
        request,
        "accessforge/confirm_delete_v2.html",
        {
            "title": f"刪除查詢：{query.name}",
            "message": "這個已儲存查詢會被移除。",
            "cancel_url": reverse("accessforge:query-builder", kwargs={"slug": table.slug}),
        },
    )


@login_required
def import_data(request, slug):
    table = _table_for_user(request.user, slug)
    require_table_editor(table, request.user)
    form = ImportDataForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["create_missing_fields"] and not table.has_role(request.user, ROLE_OWNER):
            form.add_error(
                "create_missing_fields",
                "只有資料表擁有者可以在匯入時自動建立缺少欄位。",
            )
        else:
            try:
                headers, rows = parse_uploaded_rows(form.cleaned_data["source_file"])
                summary = import_rows_to_table(
                    table,
                    headers,
                    rows,
                    user=request.user,
                    create_missing_fields=form.cleaned_data["create_missing_fields"],
                )
            except ValidationError as exc:
                form.add_error("source_file", "; ".join(exc.messages))
            else:
                messages.success(
                    request,
                    f"匯入完成，共新增 {summary['created_records']} 筆資料，建立 {summary['created_fields']} 個欄位。",
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
        messages.success(request, "表單版型已更新。")
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
def table_report(request, slug):
    table = _table_for_user(request.user, slug)
    queries = _available_queries(table, request.user)
    form = ReportBuilderForm(
        request.GET or None,
        table=table,
        user=request.user,
        queries=queries,
        initial={
            "title": f"{table.name} 報表",
            "show_row_numbers": True,
            "show_summary": True,
        },
    )

    records = list(table.records.order_by("-updated_at", "-id"))
    report_columns = _visible_field_specs(table, request.user)
    group_spec = None
    selected_query = None
    if form.is_valid():
        query_id = form.cleaned_data.get("saved_query")
        if query_id:
            selected_query = get_object_or_404(queries, pk=query_id)
            try:
                records = run_query(
                    records,
                    table,
                    selected_query.conditions,
                    selected_query.match_mode,
                    user=request.user,
                )
            except ValidationError as exc:
                form.add_error("saved_query", "; ".join(exc.messages))
                records = []
        report_columns = [
            resolve_field_path(table, request.user, token)
            for token in form.cleaned_data["columns"]
        ]
        if form.cleaned_data.get("group_by"):
            group_spec = resolve_field_path(table, request.user, form.cleaned_data["group_by"])

    groups = []
    if group_spec:
        grouped_map = {}
        for record in records:
            group_value = display_value_for_field_path(record, group_spec)
            grouped_map.setdefault(group_value, []).append(record)
        for group_label, grouped_records in grouped_map.items():
            groups.append(
                {
                    "label": group_label,
                    "rows": _build_record_rows(grouped_records, report_columns),
                    "count": len(grouped_records),
                }
            )
    else:
        groups.append(
            {
                "label": "",
                "rows": _build_record_rows(records, report_columns),
                "count": len(records),
            }
        )

    print_mode = request.GET.get("print") == "1"
    return render(
        request,
        "accessforge/report_v2.html",
        {
            "table": table,
            "form": form,
            "report_columns": report_columns,
            "groups": groups,
            "group_spec": group_spec,
            "selected_query": selected_query,
            "print_mode": print_mode,
            "report_title": (form.cleaned_data.get("title") if form.is_valid() else "") or f"{table.name} 報表",
            "show_row_numbers": form.cleaned_data.get("show_row_numbers") if form.is_valid() else True,
            "show_summary": form.cleaned_data.get("show_summary") if form.is_valid() else True,
            "total_records": len(records),
        },
    )


@login_required
def export_csv(request, slug):
    table = _table_for_user(request.user, slug)
    field_specs = _visible_field_specs(table, request.user)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = slugify(table.name) or table.slug
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    writer = csv.writer(response)
    writer.writerow([field_spec.label for field_spec in field_specs])
    for record in table.records.order_by("id"):
        writer.writerow([display_value_for_field_path(record, field_spec) for field_spec in field_specs])
    return response
