from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0022_alter_asset_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='assetfield',
            name='lookup_category',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Searchable Dropdown only: pull the options from existing assets of this category.',
                on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                to='assets.assetcategory',
            ),
        ),
    ]
