from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DataTable",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(blank=True, max_length=140, unique=True)),
                ("description", models.TextField(blank=True)),
            ],
            options={"ordering": ("name",)},
        ),
        migrations.CreateModel(
            name="DataRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("data", models.JSONField(blank=True, default=dict)),
                (
                    "table",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="records",
                        to="accessforge.datatable",
                    ),
                ),
            ],
            options={"ordering": ("-updated_at", "-id")},
        ),
        migrations.CreateModel(
            name="DataField",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(blank=True, max_length=140)),
                (
                    "field_type",
                    models.CharField(
                        choices=[
                            ("text", "單行文字"),
                            ("long_text", "多行文字"),
                            ("integer", "整數"),
                            ("decimal", "小數"),
                            ("boolean", "布林"),
                            ("date", "日期"),
                            ("datetime", "日期時間"),
                            ("email", "Email"),
                            ("url", "網址"),
                        ],
                        default="text",
                        max_length=20,
                    ),
                ),
                ("required", models.BooleanField(default=False)),
                ("default_value", models.CharField(blank=True, max_length=255)),
                ("help_text", models.CharField(blank=True, max_length=255)),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "table",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fields",
                        to="accessforge.datatable",
                    ),
                ),
            ],
            options={"ordering": ("order", "id")},
        ),
        migrations.AddConstraint(
            model_name="datafield",
            constraint=models.UniqueConstraint(
                fields=("table", "slug"),
                name="unique_table_field_slug",
            ),
        ),
    ]
