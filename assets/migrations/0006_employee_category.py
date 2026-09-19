from django.db import migrations


def create_employee_category(apps, schema_editor):
    AssetCategory = apps.get_model("assets", "AssetCategory")
    AssetCategory.objects.get_or_create(
        name="Employee",
        defaults={
            "icon": "bi-person-badge",
            "description": "Legacy 'Employee_List' sheet — employee ID / name register",
            "show_under_other": True,
        },
    )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0005_alter_asset_status'),
    ]

    operations = [
        migrations.RunPython(create_employee_category, reverse_noop),
    ]
