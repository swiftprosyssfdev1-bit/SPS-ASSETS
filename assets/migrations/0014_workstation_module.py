# Save as: assets/migrations/0014_workstation_module.py
#
# Purely additive: three new tables. No AlterField/RemoveField/DeleteModel
# on Asset, AssetHistory, or AssetCategory anywhere in this file — nothing
# about the 45 existing Workstation Asset rows changes when this runs.
#
# Rollback if ever needed:
#   python manage.py migrate assets 0013

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("assets", "0013_assetfield_assetfieldoption_configauditlog"),
    ]

    operations = [
        migrations.CreateModel(
            name="Workstation",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("extra_details", models.JSONField(blank=True, default=dict)),
                ("unresolved", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("asset", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="workstation_profile", to="assets.asset",
                )),
            ],
            options={
                "verbose_name": "Workstation",
                "verbose_name_plural": "Workstations",
            },
        ),
        migrations.CreateModel(
            name="WorkstationField",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=150)),
                ("key", models.CharField(max_length=100, unique=True)),
                ("field_type", models.CharField(choices=[
                    ("text", "Single Line Text"), ("textarea", "Multi-Line Text"),
                    ("select", "Dropdown"), ("number", "Number"), ("date", "Date"),
                    ("email", "Email"), ("url", "URL"),
                    ("lookup", "Lookup (link to another asset)"),
                ], default="text", max_length=20)),
                ("width", models.IntegerField(choices=[(6, "Half Width (col-6)"), (12, "Full Width (col-12)")], default=6)),
                ("required", models.BooleanField(default=False)),
                ("placeholder", models.CharField(blank=True, max_length=200)),
                ("help_text", models.CharField(blank=True, max_length=300)),
                ("default_value", models.CharField(blank=True, max_length=200)),
                ("show_in_list", models.BooleanField(default=True)),
                ("show_in_detail", models.BooleanField(default=True)),
                ("show_in_add", models.BooleanField(default=True)),
                ("show_in_edit", models.BooleanField(default=True)),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("lookup_category", models.CharField(blank=True, max_length=100)),
                ("lookup_multi", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Workstation Field",
                "verbose_name_plural": "Workstation Fields",
                "ordering": ["display_order", "id"],
            },
        ),
        migrations.CreateModel(
            name="WorkstationFieldOption",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=200)),
                ("value", models.CharField(max_length=200)),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("field", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="options", to="assets.workstationfield",
                )),
            ],
            options={
                "ordering": ["display_order", "id"],
            },
        ),
    ]
