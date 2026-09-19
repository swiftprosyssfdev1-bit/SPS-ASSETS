from django.db import migrations

# (name, icon, description) — each gets show_under_other=True so it lists
# under the "Other Assets" dropdown on the dashboard, same as Bluetooth
# Device / Biometric Device / Networking Equipment.
NEW_OTHER_GROUP = [
    ("Project Details", "bi-kanban", "Legacy 'Project Details' sheet — active project list"),
    ("Project Backup", "bi-archive", "Legacy 'Project backup' sheet — project backup log"),
    ("Inside Cupboard", "bi-box2", "Legacy 'inside the Cupboard' sheet — spare/unlabeled internal HDDs"),
    ("IT Vendor", "bi-truck", "Legacy 'IT_Vendor List' sheet — vendor contacts"),
    ("Incident Register", "bi-exclamation-triangle", "Legacy 'Incident Register' sheet — incident log"),
]


def create_categories(apps, schema_editor):
    AssetCategory = apps.get_model("assets", "AssetCategory")
    for name, icon, desc in NEW_OTHER_GROUP:
        AssetCategory.objects.get_or_create(
            name=name,
            defaults={"icon": icon, "description": desc, "show_under_other": True},
        )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0003_keep_existing_other_group'),
    ]

    operations = [
        migrations.RunPython(create_categories, reverse_noop),
    ]
