from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory, modelformset_factory
from django.utils.text import slugify

from .models import (
    ROLE_EDITOR,
    ROLE_CHOICES,
    ROLE_VIEWER,
    DataField,
    DataRecord,
    DataTable,
    FormLayout,
    FormLayoutField,
    SavedQuery,
    TableMembership,
)
from .services import QUERY_OPERATOR_CHOICES, get_accessible_field_paths


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
        self._apply_tooltips()

    def _apply_tooltips(self):
        for field in self.fields.values():
            tooltip = field.help_text or field.label
            if tooltip:
                field.widget.attrs["title"] = str(tooltip)


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
        self.fields["username"].label = "帳號"
        self.fields["username"].help_text = "輸入你的登入帳號。"
        self.fields["password"].label = "密碼"
        self.fields["password"].help_text = "輸入此帳號的登入密碼。"
        self._apply_tooltips()


class TableForm(StyledModelForm):
    record_label_field_slug = forms.ChoiceField(
        required=False,
        choices=[],
        help_text="選填。設定關聯欄位與查找清單要顯示哪一個欄位值。",
    )

    class Meta:
        model = DataTable
        fields = ["name", "slug", "description", "record_label_field_slug"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "資料表名稱"
        self.fields["name"].help_text = "使用者看到的主要名稱。"
        self.fields["slug"].label = "網址代稱"
        self.fields["slug"].help_text = "留空會依資料表名稱自動產生，建議只用英文、數字與連字號。"
        self.fields["description"].label = "說明"
        self.fields["description"].help_text = "簡短描述這張表的用途，方便團隊辨識。"
        self.fields["record_label_field_slug"].label = "記錄顯示欄位"
        choices = [("", "自動選擇")]
        if self.instance.pk:
            choices.extend(
                (field.slug, field.name) for field in self.instance.ordered_fields
            )
        self.fields["record_label_field_slug"].choices = choices
        self._apply_tooltips()

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
            "view_role",
            "edit_role",
            "required",
            "default_value",
            "help_text",
            "order",
        ]

    def __init__(self, *args, table: DataTable, **kwargs):
        self.table = table
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "欄位名稱"
        self.fields["name"].help_text = "顯示在表單、查詢與列表中的欄位名稱。"
        self.fields["slug"].label = "欄位代稱"
        self.fields["slug"].help_text = "留空會依欄位名稱自動產生，用於儲存與匯入比對。"
        self.fields["field_type"].label = "欄位型別"
        self.fields["field_type"].help_text = "決定輸入格式、驗證規則與查詢方式。"
        self.fields["related_table"].label = "關聯資料表"
        self.fields["related_table"].queryset = DataTable.objects.exclude(pk=table.pk).order_by("name")
        self.fields["related_table"].required = False
        self.fields["related_table"].help_text = "只有在欄位型別為「關聯記錄」時才需要設定。"
        self.fields["view_role"].label = "最小檢視權限"
        self.fields["view_role"].choices = ROLE_CHOICES
        self.fields["edit_role"].label = "最小編輯權限"
        self.fields["edit_role"].choices = ROLE_CHOICES
        self.fields["view_role"].help_text = "至少具備這個資料表角色，才看得到這個欄位。"
        self.fields["edit_role"].help_text = "至少具備這個資料表角色，才可以編輯這個欄位。"
        self.fields["required"].label = "必填"
        self.fields["required"].help_text = "啟用後，新增或編輯資料時必須輸入值。"
        self.fields["default_value"].label = "預設值"
        self.fields["default_value"].help_text = "建立新記錄時，如果未輸入內容就會使用這個值。"
        self.fields["help_text"].label = "欄位說明"
        self.fields["help_text"].help_text = "會顯示在表單下方，也會成為輸入框 tooltip。"
        self.fields["order"].label = "排序"
        self.fields["order"].help_text = "數字越小越前面，建議以 10、20、30 這種方式保留插入空間。"
        self._apply_tooltips()

    def clean_slug(self):
        slug = self.cleaned_data.get("slug", "").strip()
        name = self.cleaned_data.get("name", "").strip()
        slug = slug or slugify(name)
        queryset = DataField.objects.filter(table=self.table, slug=slug)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError("同一張資料表中的欄位代稱必須唯一。")
        return slug

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("field_type") == DataField.RELATION and not cleaned_data.get("related_table"):
            self.add_error("related_table", "請選擇這個關聯欄位要指向哪一張資料表。")
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
        user=None,
        instance: DataRecord | None = None,
        layout: FormLayout | None = None,
        **kwargs,
    ):
        self.table = table
        self.user = user
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
        self.field_permissions = {}

        for definition in self.table.ordered_fields:
            can_view = self.user is None or definition.can_view(self.user)
            can_edit = self.user is None or definition.can_edit(self.user)
            self.field_permissions[definition.slug] = {
                "view": can_view,
                "edit": can_edit,
            }
            if not can_view:
                continue
            layout_item = self.layout_item_by_slug.get(definition.slug)
            if layout_item and not layout_item.visible:
                continue
            form_field = self._build_field(definition, layout_item)
            if not can_edit:
                form_field.disabled = True
                form_field.required = False
                form_field.help_text = (
                    f"{form_field.help_text} 此欄位為唯讀。".strip()
                    if form_field.help_text
                    else "此欄位為唯讀。"
                )
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
        self._apply_tooltips()

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
                empty_label="請選擇關聯記錄",
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
            permission = self.field_permissions.get(definition.slug, {"view": False, "edit": False})
            if definition.slug in self.fields and permission["edit"]:
                value = cleaned_data.get(definition.slug)
                if definition.required and value in (None, ""):
                    self.add_error(definition.slug, "這個欄位為必填。")
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
                f"表單版型目前隱藏了沒有預設值的必填欄位：{joined}。"
            )
        return cleaned_data

    def save(self):
        record = self.instance or DataRecord(table=self.table)
        payload = {}
        for definition in self.table.ordered_fields:
            permission = self.field_permissions.get(definition.slug, {"view": True, "edit": True})
            if definition.slug in self.cleaned_data and permission["edit"]:
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
        (ROLE_VIEWER, "檢視者"),
        (ROLE_EDITOR, "編輯者"),
    ]

    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password1 = forms.CharField(required=False, widget=forms.PasswordInput)
    password2 = forms.CharField(required=False, widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=ROLE_CHOICES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "帳號"
        self.fields["username"].help_text = "若帳號已存在，會直接更新權限；不存在則建立新帳號。"
        self.fields["email"].label = "電子郵件"
        self.fields["email"].help_text = "只有建立新帳號時需要填寫。"
        self.fields["password1"].label = "初始密碼"
        self.fields["password1"].help_text = "只有建立新帳號時需要填寫。"
        self.fields["password2"].label = "確認密碼"
        self.fields["password2"].help_text = "請再次輸入密碼，避免輸入錯誤。"
        self.fields["role"].label = "角色"
        self.fields["role"].help_text = "檢視者只能看，編輯者可以新增與修改資料。"
        self._apply_tooltips()

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get("username", "").strip()
        existing_user = User.objects.filter(username=username).first()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if existing_user is None:
            if not cleaned_data.get("email"):
                self.add_error("email", "建立新帳號時必須填寫電子郵件。")
            if not password1:
                self.add_error("password1", "建立新帳號時必須設定密碼。")
            if password1 != password2:
                self.add_error("password2", "兩次輸入的密碼不一致。")
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["save_query"].label = "儲存這個查詢"
        self.fields["save_query"].help_text = "勾選後會把目前條件存成可重複使用的查詢。"
        self.fields["name"].label = "查詢名稱"
        self.fields["name"].help_text = "只有要儲存查詢時才需要填寫。"
        self.fields["description"].label = "查詢說明"
        self.fields["description"].help_text = "補充這組條件的用途，方便其他成員理解。"
        self.fields["match_mode"].label = "條件比對方式"
        self.fields["match_mode"].help_text = "決定要符合全部條件，還是只要符合其中一個。"
        self.fields["is_shared"].label = "分享給成員"
        self.fields["is_shared"].help_text = "勾選後，其他有權限的成員也能看到這個查詢。"
        self._apply_tooltips()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("save_query") and not cleaned_data.get("name"):
            self.add_error("name", "儲存查詢前請先填寫名稱。")
        return cleaned_data


class QueryConditionForm(StyledForm):
    field_path = forms.ChoiceField(required=False, choices=[])
    operator = forms.ChoiceField(required=False, choices=QUERY_OPERATOR_CHOICES)
    value = forms.CharField(required=False)

    def __init__(self, *args, table: DataTable, user=None, **kwargs):
        self.table = table
        self.user = user
        super().__init__(*args, **kwargs)
        if self.initial.get("field") and not self.initial.get("field_path"):
            self.initial["field_path"] = self.initial["field"]
        self.fields["field_path"].label = "欄位或關聯欄位"
        self.fields["field_path"].choices = [("", "請選擇欄位或關聯欄位")] + [
            (spec.token, spec.label)
            for spec in get_accessible_field_paths(table, user, include_joined=True)
        ]
        self.fields["field_path"].help_text = "可直接選本表欄位，也可以選關聯欄位做一層 join 查詢。"
        self.fields["operator"].label = "條件"
        self.fields["operator"].help_text = "選擇比對方式，例如包含、等於或大於。"
        self.fields["value"].label = "值"
        self.fields["value"].help_text = "關聯欄位可輸入記錄 ID 或完整顯示名稱。"
        self._apply_tooltips()


class BaseQueryConditionFormSet(BaseFormSet):
    def __init__(self, *args, table: DataTable, user=None, **kwargs):
        self.table = table
        self.user = user
        super().__init__(*args, **kwargs)

    def add_fields(self, form, index):
        super().add_fields(form, index)
        if "DELETE" in form.fields:
            form.fields["DELETE"].label = "刪除此條件"
            form.fields["DELETE"].help_text = "勾選後，這條規則不會被套用。"
            form.fields["DELETE"].widget.attrs["title"] = form.fields["DELETE"].help_text

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["table"] = self.table
        kwargs["user"] = self.user
        return kwargs

    def clean(self):
        if any(self.errors):
            return
        has_rule = False
        for form in self.forms:
            if form.cleaned_data.get("DELETE"):
                continue
            field_slug = form.cleaned_data.get("field_path")
            operator = form.cleaned_data.get("operator")
            value = form.cleaned_data.get("value", "")
            if field_slug or operator or value:
                has_rule = True
            if field_slug and not operator:
                form.add_error("operator", "請選擇一個查詢條件。")
        if not has_rule:
            raise ValidationError("請至少新增一條規則，或回到資料表使用快速搜尋。")


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
        help_text="當匯入檔案出現找不到的欄位時，自動建立新的文字欄位。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_file"].label = "匯入檔案"
        self.fields["source_file"].help_text = "支援 CSV 與 XLSX。第一列會被視為欄位標題。"
        self.fields["create_missing_fields"].label = "自動建立缺少欄位"
        self._apply_tooltips()


class LayoutForm(StyledModelForm):
    class Meta:
        model = FormLayout
        fields = ["title", "description", "columns", "submit_label"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].label = "表單標題"
        self.fields["title"].help_text = "顯示在資料輸入表單上方的標題。"
        self.fields["description"].label = "表單說明"
        self.fields["description"].help_text = "可放操作提示或填寫規則。"
        self.fields["columns"].label = "欄數"
        self.fields["columns"].help_text = "決定表單一列最多顯示幾個欄位區塊。"
        self.fields["submit_label"].label = "送出按鈕文字"
        self.fields["submit_label"].help_text = "例如「儲存資料」、「送出申請」。"
        self._apply_tooltips()


class LayoutItemForm(StyledModelForm):
    class Meta:
        model = FormLayoutField
        fields = ["visible", "order", "section", "label_override", "help_override", "column_span"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visible"].label = "顯示"
        self.fields["visible"].help_text = "取消勾選後，這個欄位不會出現在表單上。"
        self.fields["order"].label = "排序"
        self.fields["order"].help_text = "數字越小越前面。"
        self.fields["section"].label = "區段"
        self.fields["section"].help_text = "相同區段名稱會被排在同一個區塊。"
        self.fields["label_override"].label = "表單標籤"
        self.fields["label_override"].help_text = "只改表單上的顯示名稱，不會更動原始欄位名稱。"
        self.fields["help_override"].label = "表單提示"
        self.fields["help_override"].help_text = "只改表單上的說明文字與 tooltip。"
        self.fields["column_span"].label = "跨欄寬度"
        self.fields["column_span"].help_text = "決定這個欄位要跨幾個欄寬。"
        self._apply_tooltips()


LayoutItemFormSet = modelformset_factory(
    FormLayoutField,
    form=LayoutItemForm,
    extra=0,
)


class ReportBuilderForm(StyledForm):
    title = forms.CharField(required=False, max_length=120)
    saved_query = forms.ChoiceField(required=False, choices=[])
    columns = forms.MultipleChoiceField(
        required=False,
        choices=[],
        widget=forms.SelectMultiple(attrs={"size": 10}),
    )
    group_by = forms.ChoiceField(required=False, choices=[])
    show_row_numbers = forms.BooleanField(required=False, initial=True)
    show_summary = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, table: DataTable, user=None, queries=None, **kwargs):
        self.table = table
        self.user = user
        super().__init__(*args, **kwargs)
        field_paths = get_accessible_field_paths(table, user, include_joined=True)
        column_choices = [(spec.token, spec.label) for spec in field_paths]
        self.fields["title"].label = "報表標題"
        self.fields["title"].help_text = "會顯示在畫面與列印版最上方。"
        self.fields["saved_query"].label = "套用已儲存查詢"
        self.fields["saved_query"].help_text = "先套用查詢，再決定要顯示哪些欄位。"
        self.fields["columns"].choices = column_choices
        self.fields["columns"].label = "顯示欄位"
        self.fields["columns"].help_text = "可按住 Ctrl 或 Shift 一次選取多個欄位。"
        self.fields["group_by"].choices = [("", "不分組")] + column_choices
        self.fields["group_by"].label = "分組欄位"
        self.fields["group_by"].help_text = "選擇後會依欄位值拆成多個區塊列印。"
        self.fields["saved_query"].choices = [("", "不套用已儲存查詢")] + [
            (str(query.pk), query.name) for query in (queries or [])
        ]
        self.fields["show_row_numbers"].label = "顯示列號"
        self.fields["show_row_numbers"].help_text = "在報表左側顯示每列序號。"
        self.fields["show_summary"].label = "顯示摘要統計"
        self.fields["show_summary"].help_text = "顯示總筆數、欄位數與分組數。"
        if not self.initial.get("columns"):
            self.initial["columns"] = [spec.token for spec in field_paths[:6]]
        self._apply_tooltips()

    def clean_columns(self):
        columns = self.cleaned_data.get("columns") or []
        if not columns:
            raise ValidationError("請至少選擇一個要顯示在報表中的欄位。")
        return columns
