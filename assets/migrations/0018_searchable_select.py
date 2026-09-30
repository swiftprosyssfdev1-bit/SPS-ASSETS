from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0017_importstaging'),
    ]

    operations = [
        migrations.AlterField(
            model_name='assetfield',
            name='field_type',
            field=models.CharField(
                choices=[
                    ('text', 'Single Line Text'), ('textarea', 'Multi-Line Text'),
                    ('select', 'Dropdown'), ('searchable_select', 'Searchable Dropdown'),
                    ('number', 'Number'), ('date', 'Date'), ('email', 'Email'), ('url', 'URL'),
                ],
                default='text', max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='workstationfield',
            name='field_type',
            field=models.CharField(
                choices=[
                    ('text', 'Single Line Text'), ('textarea', 'Multi-Line Text'),
                    ('select', 'Dropdown'), ('searchable_select', 'Searchable Dropdown'),
                    ('number', 'Number'), ('date', 'Date'), ('email', 'Email'), ('url', 'URL'),
                    ('lookup', 'Lookup (link to another asset)'),
                ],
                default='text', max_length=20,
            ),
        ),
    ]
