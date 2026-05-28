# Generated migration

from django.db import migrations, models


def clamp_priority(apps, schema_editor):
    Item = apps.get_model('to_do_list', 'Item')
    Item.objects.filter(priority=5).update(priority=4)


class Migration(migrations.Migration):

    dependencies = [
        ('to_do_list', '0006_item_due_time'),
    ]

    operations = [
        migrations.RunPython(clamp_priority),
        migrations.AlterField(
            model_name='item',
            name='priority',
            field=models.PositiveSmallIntegerField(
                choices=[(4, 'Severe'), (3, 'Important'), (2, 'Mild'), (1, 'Low')],
                default=3,
            ),
        ),
    ]
