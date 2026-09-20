import secrets

from django.db import models

from catalog.models import ProductVariant, StoreProduct
from stores.models import Store


class Order(models.Model):
    """One checkout by one buyer in one store."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        PAID = "paid", "Paid"
        SENT_TO_OPS = "sent_to_ops", "Sent to production"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    buyer_name = models.CharField(max_length=200)
    buyer_email = models.EmailField()
    buyer_phone = models.CharField(max_length=30, blank=True)
    recipient_name = models.CharField(
        max_length=200, blank=True,
        help_text="Who the items are for, e.g. the player or student, if different from the buyer.",
    )
    notes = models.TextField(blank=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stripe_payment_intent = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} — {self.buyer_name}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = "IG-" + secrets.token_hex(4).upper()
        super().save(*args, **kwargs)

    def recalculate(self):
        self.subtotal = sum((i.line_total for i in self.items.all()), start=0)
        self.total = self.subtotal
        self.save(update_fields=["subtotal", "total", "updated_at"])


class OrderItem(models.Model):
    """One line of an order. Name/SKU/price are snapshotted at purchase time."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    store_product = models.ForeignKey(StoreProduct, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    variant_label = models.CharField(max_length=100, blank=True)
    sku = models.CharField(max_length=40)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} × {self.product_name} {self.variant_label}".strip()

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    def save(self, *args, **kwargs):
        if not self.product_name:
            self.product_name = self.store_product.name
        if not self.variant_label:
            self.variant_label = " / ".join(p for p in (self.variant.color, self.variant.size) if p)
        if not self.sku:
            self.sku = self.variant.sku
        if self.unit_price is None:
            self.unit_price = self.store_product.price + self.variant.price_adjustment
        super().save(*args, **kwargs)
