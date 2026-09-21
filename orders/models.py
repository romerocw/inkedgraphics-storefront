import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

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
    stripe_checkout_session = models.CharField(max_length=100, blank=True)
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
            self.unit_price = self.store_product.price + self.variant.upcharge
        super().save(*args, **kwargs)


def mark_paid(order, payment_intent=""):
    """Idempotent: safe to call from both the success page and the webhook."""
    if order.status == Order.Status.PENDING:
        order.status = Order.Status.PAID
        order.paid_at = timezone.now()
        if payment_intent:
            order.stripe_payment_intent = payment_intent
        order.save(update_fields=["status", "paid_at", "stripe_payment_intent", "updated_at"])
    return order


class OrderStatusChange(models.Model):
    """One entry in an order's status history, so staff can see who changed what."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_changes")
    from_status = models.CharField(max_length=20, choices=Order.Status.choices)
    to_status = models.CharField(max_length=20, choices=Order.Status.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="order_status_changes",
        help_text="Staff member who made the change. Blank if the system did it.",
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, help_text="Why the status changed. Required when cancelling an order.")

    class Meta:
        ordering = ["changed_at"]

    def __str__(self):
        return f"{self.order.order_number}: {self.from_status} -> {self.to_status}"


# What staff are allowed to do next, by current status. Anything not listed here is refused:
# a pending order becomes paid only through Stripe, and fulfilled/cancelled/refunded are final.
ALLOWED_TRANSITIONS = {
    Order.Status.PAID: [Order.Status.SENT_TO_OPS, Order.Status.CANCELLED],
    Order.Status.SENT_TO_OPS: [Order.Status.FULFILLED, Order.Status.CANCELLED],
}

# Cancelling loses the customer their order, so we make staff say why.
NOTE_REQUIRED_FOR = [Order.Status.CANCELLED]


def allowed_transitions(order):
    return ALLOWED_TRANSITIONS.get(order.status, [])


@transaction.atomic
def change_status(order, to_status, user=None, note=""):
    """Move an order to a new status and log it. Never touches amounts.

    Raises ValidationError if the move isn't allowed, or if a required note is missing.
    """
    note = (note or "").strip()
    if to_status not in allowed_transitions(order):
        raise ValidationError(
            f"A {order.get_status_display().lower()} order can't be marked "
            f"{Order.Status(to_status).label.lower()}."
        )
    if to_status in NOTE_REQUIRED_FOR and not note:
        raise ValidationError("Please say why you're cancelling this order.")

    from_status = order.status
    order.status = to_status
    order.save(update_fields=["status", "updated_at"])
    return OrderStatusChange.objects.create(
        order=order,
        from_status=from_status,
        to_status=to_status,
        changed_by=user if user and user.is_authenticated else None,
        note=note,
    )
