from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('to_do_list', '0009_item_repeat_unit'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='starred',
            field=models.BooleanField(default=False),
        ),
    ]
