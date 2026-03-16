from django.contrib.auth import get_user_model
from django.test import TestCase

from .forms import DynamicRecordForm
from .models import ROLE_EDITOR, DataField, DataRecord, DataTable, TableMembership
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
