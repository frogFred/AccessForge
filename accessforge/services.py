from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify

from .models import DataField, DataRecord, DataTable


QUERY_OPERATOR_CHOICES = [
    ("equals", "Equals"),
    ("not_equals", "Does not equal"),
    ("contains", "Contains"),
    ("starts_with", "Starts with"),
    ("ends_with", "Ends with"),
    ("gt", "Greater than"),
    ("gte", "Greater than or equal"),
    ("lt", "Less than"),
    ("lte", "Less than or equal"),
    ("is_empty", "Is empty"),
    ("not_empty", "Is not empty"),
    ("is_true", "Is true"),
    ("is_false", "Is false"),
]


def normalize_header(header: object, index: int) -> tuple[str, str]:
    label = str(header).strip() if header not in (None, "") else f"Column {index}"
    slug = slugify(label) or f"column_{index}"
    return label, slug


def parse_uploaded_rows(uploaded_file) -> tuple[list[str], list[list[object]]]:
    filename = uploaded_file.name.lower()
    if filename.endswith(".csv"):
        content = uploaded_file.read()
        for encoding in ("utf-8-sig", "utf-8", "cp950", "latin-1"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValidationError("Could not decode CSV file.")
        reader = csv.reader(io.StringIO(text))
        rows = [list(row) for row in reader]
    elif filename.endswith(".xlsx"):
        from openpyxl import load_workbook

        workbook = load_workbook(uploaded_file, read_only=True, data_only=True)
        sheet = workbook.active
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    else:
        raise ValidationError("Only .csv and .xlsx files are supported.")

    if not rows:
        raise ValidationError("The uploaded file is empty.")
    headers = [str(value).strip() if value is not None else "" for value in rows[0]]
    return headers, rows[1:]


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def convert_import_value(field: DataField, raw_value):
    if raw_value in (None, ""):
        return None if field.field_type not in DataField.TEXT_LIKE_TYPES else ""
    try:
        if field.field_type == DataField.TEXT:
            return str(raw_value).strip()
        if field.field_type == DataField.LONG_TEXT:
            return str(raw_value)
        if field.field_type == DataField.INTEGER:
            return int(raw_value)
        if field.field_type == DataField.DECIMAL:
            return Decimal(str(raw_value))
        if field.field_type == DataField.BOOLEAN:
            return parse_bool(raw_value)
        if field.field_type == DataField.DATE:
            if isinstance(raw_value, datetime):
                return raw_value.date()
            if isinstance(raw_value, date):
                return raw_value
            parsed = parse_date(str(raw_value))
            if parsed is None:
                raise ValidationError(f"'{raw_value}' is not a valid date for {field.name}.")
            return parsed
        if field.field_type == DataField.DATETIME:
            if isinstance(raw_value, datetime):
                return raw_value
            if isinstance(raw_value, date):
                return datetime.combine(raw_value, time.min)
            parsed = parse_datetime(str(raw_value))
            if parsed is None:
                parsed_date = parse_date(str(raw_value))
                if parsed_date is None:
                    raise ValidationError(f"'{raw_value}' is not a valid datetime for {field.name}.")
                return datetime.combine(parsed_date, time.min)
            return parsed
        if field.field_type == DataField.EMAIL:
            return str(raw_value).strip()
        if field.field_type == DataField.URL:
            return str(raw_value).strip()
        if field.field_type == DataField.RELATION:
            if field.related_table is None:
                raise ValidationError(f"{field.name} does not have a related table configured.")
            candidate = str(raw_value).strip()
            record = None
            if candidate.isdigit():
                record = field.related_table.records.filter(pk=int(candidate)).first()
            if record is None:
                matching = [
                    item
                    for item in field.related_table.records.all()
                    if item.display_label.strip().lower() == candidate.lower()
                ]
                if len(matching) == 1:
                    record = matching[0]
                elif len(matching) > 1:
                    raise ValidationError(
                        f"Relation value '{raw_value}' matches multiple records in {field.related_table.name}."
                    )
            if record is None:
                raise ValidationError(
                    f"Relation value '{raw_value}' could not be matched in {field.related_table.name}."
                )
            return record.pk
        return raw_value
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f"Invalid value '{raw_value}' for {field.name}.") from exc


@transaction.atomic
def import_rows_to_table(
    table: DataTable,
    headers: list[str],
    rows: list[list[object]],
    *,
    create_missing_fields: bool = False,
) -> dict[str, int]:
    field_map: list[tuple[int, DataField]] = []
    created_fields = 0
    current_fields = list(table.ordered_fields)
    next_order = max([field.order for field in current_fields], default=0) + 10

    for index, header in enumerate(headers, start=1):
        label, slug = normalize_header(header, index)
        field = table.fields.filter(slug=slug).first()
        if field is None:
            field = table.fields.filter(name__iexact=label).first()
        if field is None:
            if not create_missing_fields:
                raise ValidationError(
                    f"Column '{label}' does not match an existing field. "
                    "Enable auto-create missing fields to import it."
                )
            field = DataField.objects.create(
                table=table,
                name=label,
                slug=slug,
                field_type=DataField.TEXT,
                order=next_order,
            )
            next_order += 10
            created_fields += 1
        field_map.append((index - 1, field))

    created_records = 0
    for row_number, row in enumerate(rows, start=2):
        if not any(cell not in (None, "") for cell in row):
            continue

        payload = {}
        for field in table.ordered_fields:
            if field.default_value:
                payload[field.slug] = field.serialize_value(
                    field.parse_stored_value(field.default_value)
                )

        for column_index, field in field_map:
            raw_value = row[column_index] if column_index < len(row) else None
            try:
                converted = convert_import_value(field, raw_value)
            except ValidationError as exc:
                raise ValidationError(
                    f"Row {row_number}, column '{field.name}': {'; '.join(exc.messages)}"
                ) from exc
            payload[field.slug] = field.serialize_value(converted)

        missing_required = [
            field.name
            for field in table.ordered_fields
            if field.required and payload.get(field.slug) in (None, "")
        ]
        if missing_required:
            joined = ", ".join(missing_required)
            raise ValidationError(
                f"Row {row_number} is missing required fields: {joined}."
            )

        DataRecord.objects.create(table=table, data=payload)
        created_records += 1

    return {
        "created_fields": created_fields,
        "created_records": created_records,
    }


def parse_condition_value(field: DataField, raw_value: str):
    raw_value = (raw_value or "").strip()
    if raw_value == "":
        return None
    if field.field_type == DataField.INTEGER:
        return int(raw_value)
    if field.field_type == DataField.DECIMAL:
        return Decimal(raw_value)
    if field.field_type == DataField.BOOLEAN:
        return parse_bool(raw_value)
    if field.field_type == DataField.DATE:
        parsed = parse_date(raw_value)
        if parsed is None:
            raise ValidationError(f"'{raw_value}' is not a valid date.")
        return parsed
    if field.field_type == DataField.DATETIME:
        parsed = parse_datetime(raw_value)
        if parsed is None:
            raise ValidationError(f"'{raw_value}' is not a valid datetime.")
        return parsed
    if field.field_type == DataField.RELATION:
        if raw_value.isdigit():
            return int(raw_value)
        return raw_value.lower()
    return raw_value.lower()


def _normalize_record_value(field: DataField, record: DataRecord):
    raw_value = record.data.get(field.slug)
    parsed = field.parse_stored_value(raw_value)
    if field.field_type == DataField.RELATION and raw_value not in (None, ""):
        return {
            "pk": int(raw_value),
            "label": field.display_value(raw_value).lower(),
        }
    if isinstance(parsed, str):
        return parsed.lower()
    return parsed


def record_matches_condition(record: DataRecord, field: DataField, operator: str, raw_value: str) -> bool:
    current = _normalize_record_value(field, record)
    if operator == "is_empty":
        return current in (None, "", {})
    if operator == "not_empty":
        return current not in (None, "", {})
    if operator == "is_true":
        return bool(current) is True
    if operator == "is_false":
        return bool(current) is False

    expected = parse_condition_value(field, raw_value)

    if field.field_type == DataField.RELATION and isinstance(current, dict):
        if isinstance(expected, int):
            current_value = current["pk"]
        else:
            current_value = current["label"]
    else:
        current_value = current

    if operator == "equals":
        return current_value == expected
    if operator == "not_equals":
        return current_value != expected
    if operator == "contains":
        return str(expected or "") in str(current_value or "")
    if operator == "starts_with":
        return str(current_value or "").startswith(str(expected or ""))
    if operator == "ends_with":
        return str(current_value or "").endswith(str(expected or ""))
    if current_value in (None, ""):
        return False
    if operator == "gt":
        return current_value > expected
    if operator == "gte":
        return current_value >= expected
    if operator == "lt":
        return current_value < expected
    if operator == "lte":
        return current_value <= expected
    return False


def run_query(records, table: DataTable, conditions: list[dict[str, str]], match_mode: str) -> list[DataRecord]:
    active_conditions = [
        condition
        for condition in conditions
        if condition.get("field") and condition.get("operator")
    ]
    if not active_conditions:
        return list(records)

    fields_by_slug = {field.slug: field for field in table.ordered_fields}
    matched = []
    for record in records:
        checks = []
        for condition in active_conditions:
            field = fields_by_slug.get(condition["field"])
            if field is None:
                continue
            checks.append(
                record_matches_condition(
                    record,
                    field,
                    condition["operator"],
                    condition.get("value", ""),
                )
            )
        if not checks:
            matched.append(record)
        elif match_mode == "any" and any(checks):
            matched.append(record)
        elif match_mode != "any" and all(checks):
            matched.append(record)
    return matched
