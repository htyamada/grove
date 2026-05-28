# Generated migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('to_do_list', '0007_alter_item_priority'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='warning_days',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
