from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wardrobe", "0008_shared_wardrobe_categories"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="sharedwardrobeitem",
            constraint=models.UniqueConstraint(
                fields=("room", "wardrobe_item"),
                name="uq_shared_wardrobe_item_room_item",
            ),
        ),
    ]
