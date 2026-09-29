# Save as: assets/migrations/0015_sitesetting.py
#
# Additive only: one new table (SiteSetting). Nothing else is touched.
# Rollback: python manage.py migrate assets 0014

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0014_workstation_module"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=100, unique=True)),
                ("value", models.CharField(blank=True, max_length=255)),
            ],
        ),
    ]
