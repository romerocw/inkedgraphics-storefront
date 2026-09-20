from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [
        migrations.RenameField("product", "base_price", "default_price"),
        migrations.RenameField("productvariant", "price_adjustment", "upcharge"),
        migrations.AlterField(
            "product", "default_price",
            models.DecimalField(
                max_digits=8, decimal_places=2,
                help_text="Usual retail price. Pre-fills the store price; each store can override.",
            ),
        ),
        migrations.AddField(
            "product", "cost",
            models.DecimalField(
                max_digits=8, decimal_places=2, null=True, blank=True,
                help_text="Your cost (blank + decoration). Internal only; never shown to buyers.",
            ),
        ),
        migrations.AlterField(
            "productvariant", "upcharge",
            models.DecimalField(
                max_digits=8, decimal_places=2, default=0,
                help_text="Extra charged for this size only, e.g. 2.00 for 2XL. Leave 0 for standard sizes.",
            ),
        ),
    ]
