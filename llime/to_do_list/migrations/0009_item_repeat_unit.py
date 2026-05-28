from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('to_do_list', '0008_item_warning_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='repeat_unit',
            field=models.CharField(
                choices=[('days', 'days'), ('weeks', 'weeks'), ('months', 'months'), ('years', 'years')],
                default='days',
                max_length=10,
            ),
        ),
    ]
