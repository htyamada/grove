# Generated migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('to_do_list', '0005_alter_category_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='due_time',
            field=models.TimeField(default='00:00:00'),
        ),
    ]
