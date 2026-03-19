from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify


ROLE_VIEWER = "viewer"
ROLE_EDITOR = "editor"
ROLE_OWNER = "owner"
ROLE_CHOICES = [
    (ROLE_VIEWER, "檢視者"),
    (ROLE_EDITOR, "編輯者"),
    (ROLE_OWNER, "擁有者"),
]
ROLE_RANK = {
    ROLE_VIEWER: 1,
    ROLE_EDITOR: 2,
    ROLE_OWNER: 3,
}


def role_allows(actual_role: str | None, minimum_role: str) -> bool:
    return ROLE_RANK.get(actual_role, 0) >= ROLE_RANK[minimum_role]


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DataTable(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_access_tables",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    record_label_field_slug = models.SlugField(max_length=140, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
        if self.owner_id:
            TableMembership.objects.update_or_create(
                table=self,
                user=self.owner,
                defaults={"role": ROLE_OWNER},
            )

    @property
    def ordered_fields(self):
        return self.fields.select_related("related_table").order_by("order", "id")

    def get_absolute_url(self):
        return reverse("accessforge:table-detail", kwargs={"slug": self.slug})

    def get_role(self, user) -> str | None:
        if not getattr(user, "is_authenticated", False):
            return None
        if user.is_superuser or self.owner_id == user.id:
            return ROLE_OWNER
        return (
            self.memberships.filter(user=user)
            .values_list("role", flat=True)
            .first()
        )

    def has_role(self, user, minimum_role: str = ROLE_VIEWER) -> bool:
        return role_allows(self.get_role(user), minimum_role)

    def get_label_field(self):
        if not self.record_label_field_slug:
            return None
        return self.fields.filter(slug=self.record_label_field_slug).first()

    def render_record_label(self, record: "DataRecord") -> str:
        label_field = self.get_label_field()
        if label_field:
            rendered = label_field.display_value(record.data.get(label_field.slug))
            if rendered != "-":
                return rendered
        for field in self.ordered_fields:
            value = record.data.get(field.slug)
            rendered = field.display_value(value)
            if rendered != "-":
                return rendered
        return f"Record #{record.pk}"

    def get_or_create_layout(self):
        layout, _ = FormLayout.objects.get_or_create(table=self)
        layout.sync_with_fields()
        return layout


class TableMembership(TimeStampedModel):
    table = models.ForeignKey(
        DataTable,
        related_name="memberships",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="table_memberships",
        on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_VIEWER)

    class Meta:
        ordering = ("table__name", "user__username")
        constraints = [
            models.UniqueConstraint(
                fields=["table", "user"],
                name="unique_table_user_membership",
            )
        ]

    def __str__(self) -> str:
        return f"{self.table.name} / {self.user} / {self.role}"


class DataField(TimeStampedModel):
    TEXT = "text"
    LONG_TEXT = "long_text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    EMAIL = "email"
    URL = "url"
    RELATION = "relation"

    FIELD_TYPES = [
        (TEXT, "文字"),
        (LONG_TEXT, "長文字"),
        (INTEGER, "整數"),
        (DECIMAL, "小數"),
        (BOOLEAN, "布林"),
        (DATE, "日期"),
        (DATETIME, "日期時間"),
        (EMAIL, "電子郵件"),
        (URL, "網址"),
        (RELATION, "關聯記錄"),
    ]

    TEXT_LIKE_TYPES = {TEXT, LONG_TEXT, EMAIL, URL}

    table = models.ForeignKey(
        DataTable,
        related_name="fields",
        on_delete=models.CASCADE,
    )
    related_table = models.ForeignKey(
        DataTable,
        related_name="incoming_relations",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, blank=True)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default=TEXT)
    view_role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_VIEWER)
    edit_role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_EDITOR)
    required = models.BooleanField(default=False)
    default_value = models.CharField(max_length=255, blank=True)
    help_text = models.CharField(max_length=255, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["table", "slug"],
                name="unique_table_field_slug",
            )
        ]

    def __str__(self) -> str:
        return f"{self.table.name} / {self.name}"

    @property
    def is_relation(self) -> bool:
        return self.field_type == self.RELATION

    def clean(self):
        if self.field_type == self.RELATION and not self.related_table:
            raise ValidationError({"related_table": "請選擇這個關聯欄位要指向的資料表。"})
        if self.field_type != self.RELATION:
            self.related_table = None
        if ROLE_RANK[self.edit_role] < ROLE_RANK[self.view_role]:
            raise ValidationError(
                {"edit_role": "編輯權限不能低於檢視權限。"}
            )

    def can_view(self, user) -> bool:
        return self.table.has_role(user, self.view_role)

    def can_edit(self, user) -> bool:
        return self.can_view(user) and self.table.has_role(user, self.edit_role)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        previous_slug = None
        if self.pk:
            previous_slug = (
                DataField.objects.filter(pk=self.pk)
                .values_list("slug", flat=True)
                .first()
            )
        self.full_clean()
        super().save(*args, **kwargs)
        if previous_slug and previous_slug != self.slug:
            for record in self.table.records.all():
                if previous_slug in record.data and self.slug not in record.data:
                    record.data[self.slug] = record.data.pop(previous_slug)
                    record.save(update_fields=["data", "updated_at"])
        if hasattr(self.table, "form_layout"):
            self.table.form_layout.sync_with_fields()

    def delete(self, *args, **kwargs):
        slug = self.slug
        for record in self.table.records.all():
            if slug in record.data:
                record.data.pop(slug, None)
                record.save(update_fields=["data", "updated_at"])
        return super().delete(*args, **kwargs)

    def parse_stored_value(self, value):
        if value in (None, ""):
            return value
        if self.field_type == self.INTEGER:
            return int(value)
        if self.field_type == self.DECIMAL:
            return Decimal(str(value))
        if self.field_type == self.DATE and isinstance(value, str):
            return parse_date(value)
        if self.field_type == self.DATETIME and isinstance(value, str):
            return parse_datetime(value)
        if self.field_type == self.BOOLEAN:
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if self.field_type == self.RELATION:
            return int(value)
        return value

    def serialize_value(self, value):
        if self.field_type == self.BOOLEAN:
            return bool(value)
        if value in (None, ""):
            return "" if self.field_type in self.TEXT_LIKE_TYPES else None
        if self.field_type == self.INTEGER:
            return int(value)
        if self.field_type == self.DECIMAL:
            return str(value)
        if self.field_type in {self.DATE, self.DATETIME}:
            return value.isoformat()
        if self.field_type == self.RELATION:
            if isinstance(value, DataRecord):
                return value.pk
            return int(value)
        return value

    def display_value(self, value) -> str:
        if value in (None, ""):
            return "-"
        if self.field_type == self.BOOLEAN:
            return "是" if value else "否"
        if self.field_type == self.DATE:
            parsed = parse_date(value) if isinstance(value, str) else value
            return parsed.strftime("%Y-%m-%d") if parsed else str(value)
        if self.field_type == self.DATETIME:
            parsed = parse_datetime(value) if isinstance(value, str) else value
            return parsed.strftime("%Y-%m-%d %H:%M") if parsed else str(value)
        if self.field_type == self.RELATION:
            related_id = int(value)
            record = self.related_table.records.filter(pk=related_id).first() if self.related_table else None
            return record.display_label if record else f"找不到記錄 #{related_id}"
        return str(value)


class DataRecord(TimeStampedModel):
    table = models.ForeignKey(
        DataTable,
        related_name="records",
        on_delete=models.CASCADE,
    )
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-updated_at", "-id")

    def __str__(self) -> str:
        return f"{self.table.name} #{self.pk}"

    @property
    def display_label(self) -> str:
        return self.table.render_record_label(self)

    def get_value(self, field: DataField | str):
        slug = field.slug if isinstance(field, DataField) else field
        return self.data.get(slug)


class SavedQuery(TimeStampedModel):
    MATCH_ALL = "all"
    MATCH_ANY = "any"
    MATCH_CHOICES = [
        (MATCH_ALL, "符合全部條件"),
        (MATCH_ANY, "符合任一條件"),
    ]

    table = models.ForeignKey(
        DataTable,
        related_name="saved_queries",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="saved_queries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    match_mode = models.CharField(max_length=20, choices=MATCH_CHOICES, default=MATCH_ALL)
    conditions = models.JSONField(default=list, blank=True)
    is_shared = models.BooleanField(default=True)

    class Meta:
        ordering = ("table__name", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["table", "name"],
                name="unique_saved_query_name_per_table",
            )
        ]

    def __str__(self) -> str:
        return f"{self.table.name} / {self.name}"


class FormLayout(TimeStampedModel):
    table = models.OneToOneField(
        DataTable,
        related_name="form_layout",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=120, default="資料輸入表單")
    description = models.TextField(blank=True)
    columns = models.PositiveSmallIntegerField(default=2)
    submit_label = models.CharField(max_length=80, default="儲存資料")

    class Meta:
        ordering = ("table__name",)

    def __str__(self) -> str:
        return f"{self.table.name} form"

    def sync_with_fields(self):
        existing_field_ids = set(self.items.values_list("field_id", flat=True))
        next_order = (self.items.aggregate(models.Max("order")).get("order__max") or 0) + 10
        for field in self.table.ordered_fields:
            if field.id in existing_field_ids:
                continue
            FormLayoutField.objects.create(
                layout=self,
                field=field,
                order=next_order,
            )
            next_order += 10

    @property
    def ordered_items(self):
        self.sync_with_fields()
        return self.items.select_related("field").order_by("order", "id")


class FormLayoutField(TimeStampedModel):
    layout = models.ForeignKey(
        FormLayout,
        related_name="items",
        on_delete=models.CASCADE,
    )
    field = models.ForeignKey(
        DataField,
        related_name="layout_items",
        on_delete=models.CASCADE,
    )
    visible = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    section = models.CharField(max_length=120, blank=True)
    label_override = models.CharField(max_length=120, blank=True)
    help_override = models.CharField(max_length=255, blank=True)
    column_span = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ("order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["layout", "field"],
                name="unique_layout_field_item",
            )
        ]

    def __str__(self) -> str:
        return f"{self.layout.table.name} / {self.field.name}"

    @property
    def effective_label(self) -> str:
        return self.label_override or self.field.name

    @property
    def effective_help(self) -> str:
        return self.help_override or self.field.help_text
