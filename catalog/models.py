from django.db import models

from stores.models import Store


class Product(models.Model):
    """Master catalog item, shared across all clients (e.g. 'Unisex Hoodie')."""

    name = models.CharField(max_length=200)
    sku_prefix = models.CharField(max_length=20, unique=True, help_text="e.g. HOOD-U")
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="products/", blank=True)
    default_price = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="Usual retail price. Pre-fills the store price; each store can override.",
    )
    cost = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Your cost (blank + decoration). Internal only; never shown to buyers.",
    )
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
    upcharge = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        help_text="Extra charged for this size only, e.g. 2.00 for 2XL. Leave 0 for standard sizes.",
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
    """A product offered in one store, at that store's price.

    Backed by a Blank synced from ops. `product` is the old hand-made catalog, kept only until
    every store product has been re-picked against a blank; exactly one of the two is set.
    """

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="offerings")
    blank = models.ForeignKey(
        "Blank", on_delete=models.PROTECT, related_name="store_offerings", null=True, blank=True,
        help_text="The ops style this is sold from.",
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="store_offerings", null=True, blank=True,
        help_text="Legacy hand-made catalog. Being replaced by blank; don't use for new products.",
    )
    display_name = models.CharField(
        max_length=200, blank=True,
        help_text="Optional override, e.g. 'Langley Lacrosse Hoodie'. Blank = product name.",
    )
    description = models.TextField(
        blank=True, help_text="Shown to buyers. The ops catalog has no buyer-facing copy.",
    )
    image = models.ImageField(
        upload_to="store_products/", blank=True,
        help_text="Shown to buyers. Ops' supplier images are missing for most styles.",
    )
    price = models.DecimalField(max_digits=8, decimal_places=2)
    offered_variants = models.ManyToManyField(
        "BlankVariant", blank=True, related_name="offered_in",
        help_text="The colours and sizes this store sells. Empty means every active variant, "
                  "which is almost never what a school store wants.",
    )
    size_upcharges = models.JSONField(
        default=dict, blank=True,
        help_text='What buyers pay extra for a size, e.g. {"2XL": "2.00"}. Empty = the site default. '
                  "Nothing to do with what the blank costs us.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["store", "sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["store", "blank"], name="one_offering_per_blank_per_store"),
            models.UniqueConstraint(fields=["store", "product"], name="one_offering_per_product_per_store"),
            models.CheckConstraint(
                condition=models.Q(blank__isnull=False, product__isnull=True)
                | models.Q(blank__isnull=True, product__isnull=False),
                name="offering_has_exactly_one_source",
            ),
        ]

    def __str__(self):
        return f"{self.store.name}: {self.name}"

    @property
    def source(self):
        """The blank this is sold from, or the legacy product until it's re-picked."""
        return self.blank or self.product

    @property
    def name(self):
        if self.display_name:
            return self.display_name
        # Never the blank's display_title: that's supplier copy, staff-facing only.
        return self.blank.buyer_name if self.blank_id else self.product.name

    def variants(self):
        """The colours and sizes a buyer can pick.

        The chosen subset when there is one: a blank can carry a couple of hundred variants,
        and a school store sells a handful of them. Falls back to everything active, which is
        what legacy products do and what a blank does before anyone has narrowed it.
        """
        if self.blank_id and self.pk:
            chosen = self.offered_variants.filter(is_active=True)
            if chosen.exists():
                return chosen
        return self.source.variants.filter(is_active=True)

    def colors(self):
        """Distinct colours of the blank, each with its hex, for the picker's swatches."""
        seen = {}
        for variant in self.source.variants.filter(is_active=True):
            seen.setdefault(variant.color_name, variant.color_hex)
        return [{"name": name, "hex": hex_value} for name, hex_value in seen.items()]

    def upcharge_for(self, size):
        """What a buyer pays on top of `price` for this size. Blank map = the site default."""
        from decimal import Decimal

        from django.conf import settings

        table = self.size_upcharges or getattr(settings, "DEFAULT_SIZE_UPCHARGES", {}) or {}
        return Decimal(str(table.get((size or "").upper(), "0")))

    def price_for(self, variant):
        """What a buyer pays for this variant, surcharge included.

        A legacy variant carries its own upcharge, set by hand when the product was made, so
        it keeps it — moving to the site's size table must not silently reprice anything that
        hasn't been re-picked against a blank yet.
        """
        if variant is not None and hasattr(variant, "upcharge"):
            return self.price + variant.upcharge
        return self.price + self.upcharge_for(getattr(variant, "size", ""))


# --- The catalog synced from ops (API A; see docs/ops-storefront-api.md) --------------------
#
# Ops owns these rows: they are never edited here and never deleted, only marked inactive, so a
# blank that vanishes upstream can't disappear from a store that is mid-sale.

class SyncedModel(models.Model):
    """Shared plumbing for rows that belong to ops rather than to us."""

    is_active = models.BooleanField(default=True, help_text="False once ops archives it. Rows are never deleted.")
    deactivated_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When a sync first saw this go inactive. Drives the staff exceptions list.",
    )
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def apply_active(self, is_active, now):
        """Set is_active, remembering the moment it first went inactive. Returns True if changed."""
        if is_active == self.is_active:
            return False
        self.is_active = is_active
        self.deactivated_at = None if is_active else now
        return True


class Blank(SyncedModel):
    """A garment style from the ops catalog, keyed by the style id ops mints."""

    STOCK_POLICIES = [
        ("stocked", "Stocked"), ("seasonal", "Seasonal"),
        ("sell_down", "Selling down"), ("job_only", "Ordered per job"),
    ]
    AUDIENCES = [("adult", "Adult"), ("youth", "Youth"), ("toddler", "Toddler")]

    style_id = models.PositiveIntegerField(unique=True, help_text="Ops style ID. Our key for a blank.")
    supplier_style_code = models.CharField(
        max_length=64, db_index=True,
        help_text="Ops style_name, e.g. CC1567 — the canonical code, not a supplier's internal ID.",
    )
    brand = models.CharField(max_length=120, blank=True)
    display_title = models.CharField(
        max_length=255, blank=True,
        help_text="Supplier catalog copy. STAFF-FACING ONLY — never show it to buyers; "
                  "use the store's own name for the product, or merch_label.",
    )
    merch_label = models.CharField(
        max_length=200, blank=True,
        help_text="Customer-facing garment name from ops marketing. Blank on most styles today.",
    )
    category = models.CharField(max_length=120, blank=True, help_text="Ops category name, free text.")
    audience = models.CharField(max_length=20, choices=AUDIENCES, blank=True)
    stock_policy = models.CharField(max_length=20, choices=STOCK_POLICIES, blank=True)
    base_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="What ops pays today for the cheapest active variant. Internal only, never shown to buyers.",
    )
    currency = models.CharField(max_length=3, default="USD")
    size_chart = models.JSONField(null=True, blank=True, help_text="Always null in API v1.")
    ops_updated_at = models.DateTimeField(help_text="The style's updated_at on ops, as sent.")

    class Meta:
        ordering = ["brand", "supplier_style_code"]

    def __str__(self):
        return f"{self.supplier_style_code} — {self.buyer_name}"

    @property
    def buyer_name(self):
        """A name safe to show a buyer. Never display_title, which is supplier copy."""
        return self.merch_label or " ".join(p for p in (self.brand, self.supplier_style_code) if p)


class BlankVariant(SyncedModel):
    """One sellable colour and size of a blank, keyed by the SKU ops mints."""

    blank = models.ForeignKey(Blank, on_delete=models.CASCADE, related_name="variants")
    blank_sku = models.CharField(max_length=100, unique=True, help_text="Ops-minted. Our key for a variant.")
    color_name = models.CharField(max_length=80, help_text="Ops colour token, e.g. BlueJean.")
    color_hex = models.CharField(max_length=7, blank=True)
    size = models.CharField(max_length=20, blank=True)
    size_sort_order = models.IntegerField(
        null=True, blank=True, help_text="Sort ascending for display. Not contiguous.",
    )
    cost_adjustment = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="This variant's cost minus the style's base cost. Our cost, not a buyer upcharge.",
    )
    currency = models.CharField(max_length=3, default="USD")
    stock_policy = models.CharField(max_length=20, choices=Blank.STOCK_POLICIES, blank=True)

    class Meta:
        ordering = ["blank", "color_name", "size_sort_order", "size"]

    def __str__(self):
        return self.blank_sku

    @property
    def label(self):
        return " / ".join(p for p in (self.color_name, self.size) if p)

    # Named like ProductVariant so templates, the cart and OrderItem can treat the two alike
    # while store products are being moved across. They go away with ProductVariant.
    @property
    def color(self):
        return self.color_name

    @property
    def sku(self):
        return self.blank_sku

    @property
    def sort_order(self):
        return self.size_sort_order or 0


class BlankImage(models.Model):
    """A supplier CDN image for one colour. Best-effort: ops may have none for a style."""

    blank = models.ForeignKey(Blank, on_delete=models.CASCADE, related_name="images")
    color_name = models.CharField(max_length=80, help_text="Matches a variant's color_name.")
    image_type = models.CharField(max_length=40, blank=True, help_text="Supplier's own label: large, swatch, …")
    view = models.CharField(max_length=20, blank=True, help_text="Always blank in API v1; reserved for front/back.")
    url = models.URLField(
        max_length=500,
        help_text="Supplier CDN, hotlinked. It can change or vanish — cache anything we rely on.",
    )

    class Meta:
        ordering = ["color_name", "image_type"]

    def __str__(self):
        return f"{self.blank.supplier_style_code} {self.color_name} ({self.image_type})"
