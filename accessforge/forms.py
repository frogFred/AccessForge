from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory, modelformset_factory
from django.utils.text import slugify

from .models import (
    ROLE_EDITOR,
    ROLE_VIEWER,
    DataField,
    DataRecord,
    DataTable,
    FormLayout,
    FormLayoutField,
    SavedQuery,
    TableMembership,
)
from .services import QUERY_OPERATOR_CHOICES


User = get_user_model()


class StyledFieldsMixin:
    def _apply_field_styles(self):
        for field in self.fields.values():
            widget = field.widget
            css_classes = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{css_classes} form-control".strip()
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "form-checkbox"
            if isinstance(widget, forms.FileInput):
                widget.attrs["class"] = "form-file"
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 4)


class StyledModelForm(StyledFieldsMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_field_styles()


class StyledForm(StyledFieldsMixin, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_field_styles()


class StyledAuthenticationForm(StyledFieldsMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_field_styles()


class TableForm(StyledModelForm):
    record_label_field_slug = forms.ChoiceField(
        required=False,
        choices=[],
        help_text="Optional field used as the display label for relations and lookups.",
    )

    class Meta:
        model = DataTable
        fields = ["name", "slug", "description", "record_label_field_slug"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "Automatic")]
        if self.instance.pk:
            choices.extend(
                (field.slug, field.name) for field in self.instance.ordered_fields
            )
        self.fields["record_label_field_slug"].choices = choices

    def clean_slug(self):
        slug = self.cleaned_data.get("slug", "").strip()
        name = self.cleaned_data.get("name", "").strip()
        return slug or slugify(name)


class FieldForm(StyledModelForm):
    class Meta:
        model = DataField
        fields = [
            "name",
            "slug",
            "field_type",
            "related_table",
            "required",
            "default_value",
            "help_text",
            "order",
        ]

    def __init__(self, *args, table: DataTable, **kwargs):
        self.table = table
        super().__init__(*args, **kwargs)
        self.fields["related_table"].queryset = DataTable.objects.exclude(pk=table.pk).order_by("name")
        self.fields["related_table"].required = False
        self.fields["related_table"].help_text = "Only used when the field type is 'Related record'."

    def clean_slug(self):
        slug = self.cleaned_data.get("slug", "").strip()
        name = self.cleaned_data.get("name", "").strip()
        slug = slug or slugify(name)
        queryset = DataField.objects.filter(table=self.table, slug=slug)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError("Field slugs must be unique within the same table.")
        return slug

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("field_type") == DataField.RELATION and not cleaned_data.get("related_table"):
            self.add_error("related_table", "Choose the table this relation points to.")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.table = self.table
        if commit:
            instance.save()
        return instance


class RecordChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.display_label


class DynamicRecordForm(StyledForm):
    def __init__(
        self,
        *args,
        table: DataTable,
        instance: DataRecord | None = None,
        layout: FormLayout | None = None,
        **kwargs,
    ):
        self.table = table
        self.instance = instance
        self.layout = layout or table.get_or_create_layout()
        self.layout.sync_with_fields()
        super().__init__(*args, **kwargs)
        initial_data = instance.data if instance else {}
        self.layout_item_by_slug = {
            item.field.slug: item
            for item in self.layout.ordered_items
        }
        self.visible_fields = []

        for definition in self.table.ordered_fields:
            layout_item = self.layout_item_by_slug.get(definition.slug)
            if layout_item and not layout_item.visible:
                continue
            form_field = self._build_field(definition, layout_item)
            self.fields[definition.slug] = form_field
            self.visible_fields.append(definition)
            if definition.slug in initial_data:
                self.initial[definition.slug] = definition.parse_stored_value(
                    initial_data.get(definition.slug)
                )
            elif definition.default_value:
                self.initial[definition.slug] = definition.parse_stored_value(
                    definition.default_value
                )

    def _build_field(self, definition: DataField, layout_item: FormLayoutField | None):
        label = layout_item.effective_label if layout_item else definition.name
        help_text = layout_item.effective_help if layout_item else definition.help_text
        common_kwargs = {
            "label": label,
            "help_text": help_text,
        }
        if definition.field_type == DataField.TEXT:
            return forms.CharField(**common_kwargs, required=definition.required)
        if definition.field_type == DataField.LONG_TEXT:
            return forms.CharField(
                **common_kwargs,
                required=definition.required,
                widget=forms.Textarea(attrs={"rows": 5}),
            )
        if definition.field_type == DataField.INTEGER:
            return forms.IntegerField(**common_kwargs, required=definition.required)
        if definition.field_type == DataField.DECIMAL:
            return forms.DecimalField(
                **common_kwargs,
                required=definition.required,
                decimal_places=2,
                max_digits=12,
                widget=forms.NumberInput(attrs={"step": "0.01"}),
            )
        if definition.field_type == DataField.BOOLEAN:
            return forms.BooleanField(**common_kwargs, required=False)
        if definition.field_type == DataField.DATE:
            return forms.DateField(
                **common_kwargs,
                required=definition.required,
                widget=forms.DateInput(attrs={"type": "date"}),
            )
        if definition.field_type == DataField.DATETIME:
            return forms.DateTimeField(
                **common_kwargs,
                required=definition.required,
                input_formats=["%Y-%m-%dT%H:%M"],
                widget=forms.DateTimeInput(
                    attrs={"type": "datetime-local"},
                    format="%Y-%m-%dT%H:%M",
                ),
            )
        if definition.field_type == DataField.EMAIL:
            return forms.EmailField(**common_kwargs, required=definition.required)
        if definition.field_type == DataField.URL:
            return forms.URLField(**common_kwargs, required=definition.required)
        if definition.field_type == DataField.RELATION and definition.related_table:
            return RecordChoiceField(
                **common_kwargs,
                queryset=definition.related_table.records.order_by("id"),
                required=definition.required,
                empty_label="Select a related record",
            )
        return forms.CharField(**common_kwargs, required=definition.required)

    def get_layout_sections(self):
        sections = []
        current_title = None
        current_fields = []
        ordered_items = [
            item
            for item in self.layout.ordered_items
            if item.visible and item.field.slug in self.fields
        ]
        for item in ordered_items:
            title = item.section.strip() if item.section else ""
            if title != current_title:
                if current_fields:
                    sections.append({"title": current_title, "fields": current_fields})
                current_title = title
                current_fields = []
            current_fields.append(
                {
                    "bound_field": self[item.field.slug],
                    "span": min(max(item.column_span, 1), max(self.layout.columns, 1)),
                }
            )
        if current_fields:
            sections.append({"title": current_title, "fields": current_fields})
        if not sections:
            sections.append(
                {
                    "title": "",
                    "fields": [
                        {"bound_field": self[field.slug], "span": 1}
                        for field in self.visible_fields
                    ],
                }
            )
        return sections

    def clean(self):
        cleaned_data = super().clean()
        hidden_required = []
        for definition in self.table.ordered_fields:
            if definition.slug in self.fields:
                value = cleaned_data.get(definition.slug)
                if definition.required and value in (None, ""):
                    self.add_error(definition.slug, "This field is required.")
            else:
                existing_value = None
                if self.instance:
                    existing_value = self.instance.data.get(definition.slug)
                elif definition.default_value:
                    existing_value = definition.default_value
                if definition.required and existing_value in (None, ""):
                    hidden_required.append(definition.name)
        if hidden_required:
            joined = ", ".join(hidden_required)
            raise ValidationError(
                f"The form layout hides required fields without defaults: {joined}."
            )
        return cleaned_data

    def save(self):
        record = self.instance or DataRecord(table=self.table)
        payload = {}
        for definition in self.table.ordered_fields:
            if definition.slug in self.cleaned_data:
                raw_value = self.cleaned_data.get(definition.slug)
                payload[definition.slug] = definition.serialize_value(raw_value)
                continue
            if self.instance and definition.slug in self.instance.data:
                payload[definition.slug] = self.instance.data.get(definition.slug)
                continue
            if definition.default_value:
                payload[definition.slug] = definition.serialize_value(
                    definition.parse_stored_value(definition.default_value)
                )
                continue
            payload[definition.slug] = definition.serialize_value(None)
        record.data = payload
        record.save()
        return record


class MembershipAssignmentForm(StyledForm):
    ROLE_CHOICES = [
        (ROLE_VIEWER, "Viewer"),
        (ROLE_EDITOR, "Editor"),
    ]

    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password1 = forms.CharField(required=False, widget=forms.PasswordInput)
    password2 = forms.CharField(required=False, widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=ROLE_CHOICES)

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username", "").strip()
        existing_user = User.objects.filter(username=username).first()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if existing_user is None:
            if not cleaned_data.get("email"):
                self.add_error("email", "Email is required when creating a new user.")
            if not password1:
                self.add_error("password1", "Password is required when creating a new user.")
            if password1 != password2:
                self.add_error("password2", "Passwords must match.")
        self.user_instance = existing_user
        return cleaned_data

    def save(self, table: DataTable):
        user = self.user_instance
        if user is None:
            user = User.objects.create_user(
                username=self.cleaned_data["username"].strip(),
                email=self.cleaned_data["email"],
                password=self.cleaned_data["password1"],
            )
        membership, _ = TableMembership.objects.update_or_create(
            table=table,
            user=user,
            defaults={"role": self.cleaned_data["role"]},
        )
        return membership


class QueryBuilderForm(StyledModelForm):
    save_query = forms.BooleanField(required=False, initial=False)

    class Meta:
        model = SavedQuery
        fields = ["name", "description", "match_mode", "is_shared"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("save_query") and not cleaned_data.get("name"):
            self.add_error("name", "A saved query needs a name.")
        return cleaned_data


class QueryConditionForm(StyledForm):
    field = forms.ChoiceField(required=False, choices=[])
    operator = forms.ChoiceField(required=False, choices=QUERY_OPERATOR_CHOICES)
    value = forms.CharField(required=False)

    def __init__(self, *args, table: DataTable, **kwargs):
        self.table = table
        super().__init__(*args, **kwargs)
        self.fields["field"].choices = [("", "Select field")] + [
            (field.slug, field.name) for field in table.ordered_fields
        ]
        self.fields["value"].help_text = "Use record id or exact label for relation fields."


class BaseQueryConditionFormSet(BaseFormSet):
    def __init__(self, *args, table: DataTable, **kwargs):
        self.table = table
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["table"] = self.table
        return kwargs

    def clean(self):
        if any(self.errors):
            return
        has_rule = False
        for form in self.forms:
            if form.cleaned_data.get("DELETE"):
                continue
            field_slug = form.cleaned_data.get("field")
            operator = form.cleaned_data.get("operator")
            value = form.cleaned_data.get("value", "")
            if field_slug or operator or value:
                has_rule = True
            if field_slug and not operator:
                form.add_error("operator", "Choose an operator.")
        if not has_rule:
            raise ValidationError("Add at least one rule or use the quick search instead.")


QueryConditionFormSet = formset_factory(
    QueryConditionForm,
    formset=BaseQueryConditionFormSet,
    extra=4,
    can_delete=True,
)


class ImportDataForm(StyledForm):
    source_file = forms.FileField()
    create_missing_fields = forms.BooleanField(
        required=False,
        help_text="Create new text fields when a column is not found.",
    )


class LayoutForm(StyledModelForm):
    class Meta:
        model = FormLayout
        fields = ["title", "description", "columns", "submit_label"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class LayoutItemForm(StyledModelForm):
    class Meta:
        model = FormLayoutField
        fields = ["visible", "order", "section", "label_override", "help_override", "column_span"]


LayoutItemFormSet = modelformset_factory(
    FormLayoutField,
    form=LayoutItemForm,
    extra=0,
)
