from django.db import migrations

EXISTING_OTHER_GROUP = ["Bluetooth Device", "Biometric Device", "Networking Equipment"]


def set_show_under_other(apps, schema_editor):
    AssetCategory = apps.get_model("assets", "AssetCategory")
    AssetCategory.objects.filter(name__in=EXISTING_OTHER_GROUP).update(show_under_other=True)


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_assetcategory_show_under_other'),
    ]

    operations = [
        migrations.RunPython(set_show_under_other, reverse_noop),
    ]
