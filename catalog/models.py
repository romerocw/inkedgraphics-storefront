from django.db import models

from stores.models import Store


class Product(models.Model):
    """Master catalog item, shared across all clients (e.g. 'Unisex Hoodie')."""

    name = models.CharField(max_length=200)
    sku_prefix = models.CharField(max_length=20, unique=True, help_text="e.g. HOOD-U")
    description = models.TextField(blank=True)
    base_price = models.DecimalField(max_digits=8, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    """A sellable size/color of a product, with its own SKU."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    color = models.CharField(max_length=50, blank=True)
    size = models.CharField(max_length=20, blank=True)
    sku = models.CharField(max_length=40, unique=True)
    price_adjustment = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Added to the store price, e.g. +2.00 for 2XL.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["product", "sort_order", "color", "size"]
        unique_together = [("product", "color", "size")]

    def __str__(self):
        parts = [self.product.name, self.color, self.size]
        return " / ".join(p for p in parts if p)


class StoreProduct(models.Model):
    """A product offered in one store, at that store's price."""

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="offerings")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="store_offerings")
    display_name = models.CharField(
        max_length=200, blank=True,
        help_text="Optional override, e.g. 'Langley Lacrosse Hoodie'. Blank = product name.",
    )
    price = models.DecimalField(max_digits=8, decimal_places=2)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["store", "sort_order"]
        unique_together = [("store", "product")]

    def __str__(self):
        return f"{self.store.name}: {self.display_name or self.product.name}"

    @property
    def name(self):
        return self.display_name or self.product.name
