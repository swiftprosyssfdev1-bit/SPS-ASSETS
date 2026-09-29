from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0012_exportpassword'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AssetField',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(max_length=150)),
                ('key', models.CharField(
                    help_text='Stable internal key stored in Asset.extra_details. Auto-generated on creation; immutable afterwards.',
                    max_length=100,
                )),
                ('field_type', models.CharField(
                    choices=[
                        ('text', 'Single Line Text'),
                        ('textarea', 'Multi-Line Text'),
                        ('select', 'Dropdown'),
                        ('number', 'Number'),
                        ('date', 'Date'),
                        ('email', 'Email'),
                        ('url', 'URL'),
                    ],
                    default='text',
                    max_length=20,
                )),
                ('width', models.IntegerField(
                    choices=[(6, 'Half Width (col-6)'), (12, 'Full Width (col-12)')],
                    default=6,
                )),
                ('required', models.BooleanField(default=False)),
                ('placeholder', models.CharField(blank=True, max_length=200)),
                ('help_text', models.CharField(blank=True, max_length=300)),
                ('default_value', models.CharField(blank=True, max_length=200)),
                ('show_in_list', models.BooleanField(default=True)),
                ('show_in_detail', models.BooleanField(default=True)),
                ('show_in_add', models.BooleanField(default=True)),
                ('show_in_edit', models.BooleanField(default=True)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='fields',
                    to='assets.assetcategory',
                )),
            ],
            options={
                'verbose_name': 'Asset Field',
                'verbose_name_plural': 'Asset Fields',
                'ordering': ['display_order', 'id'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='assetfield',
            unique_together={('category', 'key')},
        ),
        migrations.CreateModel(
            name='AssetFieldOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(max_length=200)),
                ('value', models.CharField(
                    help_text='The value stored in Asset.extra_details. Defaults to label if left blank.',
                    max_length=200,
                )),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('field', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='options',
                    to='assets.assetfield',
                )),
            ],
            options={
                'ordering': ['display_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='ConfigAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('changed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('action', models.CharField(
                    choices=[
                        ('create', 'Create'),
                        ('update', 'Update'),
                        ('deactivate', 'Deactivate'),
                        ('delete', 'Delete'),
                    ],
                    max_length=20,
                )),
                ('model_name', models.CharField(max_length=50)),
                ('object_id', models.IntegerField(blank=True, null=True)),
                ('object_repr', models.CharField(max_length=255)),
                ('diff', models.TextField(blank=True)),
                ('changed_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Config Audit Log',
                'verbose_name_plural': 'Config Audit Logs',
                'ordering': ['-changed_at'],
            },
        ),
    ]
