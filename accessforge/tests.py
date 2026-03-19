from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .forms import DynamicRecordForm
from .models import ROLE_EDITOR, ROLE_OWNER, DataField, DataRecord, DataTable, TableMembership
from .services import import_rows_to_table, run_query


User = get_user_model()


class AccessForgeTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pw123456")
        self.editor = User.objects.create_user("editor", password="pw123456")

    def test_dynamic_record_form_saves_typed_values(self):
        table = DataTable.objects.create(name="Customers", owner=self.owner)
        DataField.objects.create(table=table, name="Name", field_type=DataField.TEXT, required=True)
        DataField.objects.create(table=table, name="Age", field_type=DataField.INTEGER)
        DataField.objects.create(table=table, name="Active", field_type=DataField.BOOLEAN)

        form = DynamicRecordForm(
            data={"name": "Ada", "age": "42", "active": "on"},
            table=table,
            layout=table.get_or_create_layout(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        record = form.save()
        self.assertEqual(record.data["name"], "Ada")
        self.assertEqual(record.data["age"], 42)
        self.assertTrue(record.data["active"])

    def test_relation_field_saves_related_record_id(self):
        customers = DataTable.objects.create(name="Customers", owner=self.owner, record_label_field_slug="name")
        DataField.objects.create(table=customers, name="Name", slug="name", field_type=DataField.TEXT)
        customer_record = DataRecord.objects.create(table=customers, data={"name": "Acme"})

        orders = DataTable.objects.create(name="Orders", owner=self.owner)
        DataField.objects.create(table=orders, name="Order no", slug="order_no", field_type=DataField.TEXT)
        DataField.objects.create(
            table=orders,
            name="Customer",
            slug="customer",
            field_type=DataField.RELATION,
            related_table=customers,
        )

        form = DynamicRecordForm(
            data={"order_no": "SO-1", "customer": str(customer_record.pk)},
            table=orders,
            layout=orders.get_or_create_layout(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        record = form.save()
        self.assertEqual(record.data["customer"], customer_record.pk)

    def test_editor_membership_grants_edit_access(self):
        table = DataTable.objects.create(name="Projects", owner=self.owner)
        TableMembership.objects.create(table=table, user=self.editor, role=ROLE_EDITOR)
        self.assertTrue(table.has_role(self.editor, ROLE_EDITOR))

    def test_query_runner_filters_records(self):
        table = DataTable.objects.create(name="Invoices", owner=self.owner)
        DataField.objects.create(table=table, name="Code", slug="code", field_type=DataField.TEXT)
        DataField.objects.create(table=table, name="Total", slug="total", field_type=DataField.INTEGER)
        DataRecord.objects.create(table=table, data={"code": "A-100", "total": 50})
        matching = DataRecord.objects.create(table=table, data={"code": "B-200", "total": 120})

        results = run_query(
            table.records.order_by("id"),
            table,
            [{"field": "total", "operator": "gte", "value": "100"}],
            "all",
        )

        self.assertEqual([record.pk for record in results], [matching.pk])

    def test_import_rows_can_create_missing_fields(self):
        table = DataTable.objects.create(name="Imports", owner=self.owner)
        summary = import_rows_to_table(
            table,
            ["Name", "Budget"],
            [["Bridge", "1200"], ["Road", "950"]],
            create_missing_fields=True,
        )

        self.assertEqual(summary["created_fields"], 2)
        self.assertEqual(summary["created_records"], 2)
        self.assertEqual(table.fields.count(), 2)
        self.assertEqual(table.records.count(), 2)

    def test_field_level_permissions_hide_locked_fields_and_preserve_values(self):
        table = DataTable.objects.create(name="Projects", owner=self.owner)
        DataField.objects.create(table=table, name="Title", slug="title", field_type=DataField.TEXT)
        DataField.objects.create(
            table=table,
            name="Internal notes",
            slug="internal_notes",
            field_type=DataField.TEXT,
            view_role=ROLE_OWNER,
            edit_role=ROLE_OWNER,
        )
        TableMembership.objects.create(table=table, user=self.editor, role=ROLE_EDITOR)
        record = DataRecord.objects.create(
            table=table,
            data={"title": "Old title", "internal_notes": "Owner only"},
        )

        form = DynamicRecordForm(
            data={"title": "New title"},
            table=table,
            user=self.editor,
            instance=record,
            layout=table.get_or_create_layout(),
        )

        self.assertNotIn("internal_notes", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.data["title"], "New title")
        self.assertEqual(updated.data["internal_notes"], "Owner only")

    def test_join_query_can_filter_on_related_field(self):
        customers = DataTable.objects.create(name="Customers", owner=self.owner, record_label_field_slug="name")
        DataField.objects.create(table=customers, name="Name", slug="name", field_type=DataField.TEXT)
        acme = DataRecord.objects.create(table=customers, data={"name": "Acme"})
        globex = DataRecord.objects.create(table=customers, data={"name": "Globex"})

        orders = DataTable.objects.create(name="Orders", owner=self.owner)
        DataField.objects.create(table=orders, name="Order no", slug="order_no", field_type=DataField.TEXT)
        DataField.objects.create(
            table=orders,
            name="Customer",
            slug="customer",
            field_type=DataField.RELATION,
            related_table=customers,
        )
        match = DataRecord.objects.create(table=orders, data={"order_no": "SO-1", "customer": acme.pk})
        DataRecord.objects.create(table=orders, data={"order_no": "SO-2", "customer": globex.pk})

        results = run_query(
            orders.records.order_by("id"),
            orders,
            [{"field_path": "customer__name", "operator": "contains", "value": "acme"}],
            "all",
            user=self.owner,
        )

        self.assertEqual([record.pk for record in results], [match.pk])

    def test_import_rejects_non_editable_field_for_editor(self):
        table = DataTable.objects.create(name="Budgets", owner=self.owner)
        DataField.objects.create(
            table=table,
            name="Budget",
            slug="budget",
            field_type=DataField.INTEGER,
            view_role=ROLE_EDITOR,
            edit_role=ROLE_OWNER,
        )
        TableMembership.objects.create(table=table, user=self.editor, role=ROLE_EDITOR)

        with self.assertRaises(ValidationError):
            import_rows_to_table(
                table,
                ["Budget"],
                [["1200"]],
                user=self.editor,
            )

    def test_query_builder_page_lists_join_paths(self):
        self.client.force_login(self.owner)
        customers = DataTable.objects.create(name="Accounts", owner=self.owner)
        DataField.objects.create(table=customers, name="Name", slug="name", field_type=DataField.TEXT)

        orders = DataTable.objects.create(name="Order index", owner=self.owner)
        DataField.objects.create(table=orders, name="Order no", slug="order_no", field_type=DataField.TEXT)
        DataField.objects.create(
            table=orders,
            name="Customer",
            slug="customer",
            field_type=DataField.RELATION,
            related_table=customers,
        )

        response = self.client.get(reverse("accessforge:query-builder", kwargs={"slug": orders.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "可用關聯欄位")
        self.assertContains(response, "Customer -&gt; Name")

    def test_report_view_renders_joined_columns(self):
        self.client.force_login(self.owner)
        customers = DataTable.objects.create(name="Clients", owner=self.owner, record_label_field_slug="name")
        DataField.objects.create(table=customers, name="Name", slug="name", field_type=DataField.TEXT)
        acme = DataRecord.objects.create(table=customers, data={"name": "Acme"})

        orders = DataTable.objects.create(name="Sales orders", owner=self.owner)
        DataField.objects.create(table=orders, name="Order no", slug="order_no", field_type=DataField.TEXT)
        DataField.objects.create(
            table=orders,
            name="Customer",
            slug="customer",
            field_type=DataField.RELATION,
            related_table=customers,
        )
        DataRecord.objects.create(table=orders, data={"order_no": "SO-9", "customer": acme.pk})

        response = self.client.get(
            reverse("accessforge:table-report", kwargs={"slug": orders.slug}),
            {
                "title": "Orders by customer",
                "columns": ["order_no", "customer__name"],
                "group_by": "customer__name",
                "show_row_numbers": "on",
                "show_summary": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Orders by customer")
        self.assertContains(response, "來源資料表")
        self.assertContains(response, "Acme")
