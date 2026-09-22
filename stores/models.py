from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models
from django.templatetags.tz import do_timezone
from django.utils import timezone

# Where a store's open/close times are meant. Times are typed and shown in the store's zone,
# whoever is looking; everything else on the site uses settings.TIME_ZONE (Eastern).
DEFAULT_TIME_ZONE = "America/New_York"
TIME_ZONES = [
    ("America/New_York", "Eastern"),
    ("America/Chicago", "Central"),
    ("America/Denver", "Mountain"),
    ("America/Phoenix", "Arizona (no daylight saving)"),
    ("America/Los_Angeles", "Pacific"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
]


def share_asset_path(instance, filename):
    """Share-kit files live under their own store's prefix, never mixed together."""
    return f"stores/{instance.slug}/share/{filename}"


class Client(models.Model):
    """An organization we run a private-labeled store for (school, department, business)."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, help_text="Used in URLs; lowercase, no spaces.")
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    logo = models.ImageField(upload_to="clients/logos/", blank=True, help_text="Shown in the store header.")
    primary_color = models.CharField(
        max_length=7, default="#111827",
        help_text="Hex color for the store header and buttons, e.g. #1e3a8a.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Store(models.Model):
    """A single group store with an open/close window."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    class Fulfillment(models.TextChoices):
        # Three genuinely different products, not two wordings of one. Shipping (either kind)
        # is priced from ShipStation; delivery is us driving a few boxes to a local school.
        INDIVIDUAL_SHIP = "individual_ship", "Ship to each buyer"
        GROUP_SHIP = "group_ship", "Ship one consignment to the organization"
        GROUP_DELIVERY = "group_delivery", "Deliver to the organization ourselves"

    GROUP_MODES = (Fulfillment.GROUP_SHIP, Fulfillment.GROUP_DELIVERY)

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="stores")
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, help_text="Store URL path, e.g. store.inkedgraphics.com/<slug>/")
    subdomain = models.CharField(
        max_length=63, blank=True, null=True, unique=True,
        help_text="Optional: <subdomain>.inkedgraphics.com for clients who want their own host.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    primary_color = models.CharField(
        max_length=7, blank=True,
        help_text="Hex color for this store, e.g. #228B22. Blank = use the client's color.",
    )
    time_zone = models.CharField(
        max_length=40, choices=TIME_ZONES, default=DEFAULT_TIME_ZONE,
        help_text="The store's local time zone. Open and close times are typed and shown in it.",
    )
    opens_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When a scheduled store opens by itself, in the store's time zone. Blank = open it by hand.",
    )
    closes_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When an open store closes by itself, in the store's time zone. Blank = close it by hand.",
    )
    fulfillment_mode = models.CharField(
        max_length=20, choices=Fulfillment.choices, default=Fulfillment.INDIVIDUAL_SHIP,
        help_text="How this store's orders reach their buyers.",
    )
    delivery_location_name = models.CharField(
        max_length=200, blank=True,
        help_text="Where a group order is handed over, e.g. 'Langley High front office'.",
    )
    delivery_address = models.TextField(
        blank=True, help_text="Street address the whole group order goes to. Shown to buyers.",
    )
    delivery_contact_name = models.CharField(max_length=200, blank=True)
    delivery_contact_phone = models.CharField(max_length=30, blank=True)
    group_ship_fee = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Flat delivery charge added to each buyer's order in a group-ship store. "
                  "Quote it from ShipStation when setting the store up. Blank = charge nothing.",
    )
    group_delivery_fee = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="What the organization is billed for the whole drop-off. Never charged to "
                  "buyers, never sent to Stripe. Blank = the site default.",
    )
    production_lead_days = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Business days to produce this store's order once it closes. Blank = the site default.",
    )
    ship_days_estimate = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Business days in transit after production. Blank = the site default.",
    )
    arrival_buffer_days = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="How many business days wide the arrival range is. Blank = the site default.",
    )
    share_qr_png = models.FileField(upload_to=share_asset_path, blank=True)
    share_qr_svg = models.FileField(upload_to=share_asset_path, blank=True)
    share_flyer_pdf = models.FileField(upload_to=share_asset_path, blank=True)
    share_social_png = models.FileField(upload_to=share_asset_path, blank=True)
    share_kit_fingerprint = models.CharField(
        max_length=64, blank=True,
        help_text="Hash of what's printed on the share kit. Unchanged = nothing to rebuild.",
    )
    share_kit_generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client} — {self.name}"

    @property
    def has_share_kit(self):
        return bool(self.share_kit_generated_at and self.share_social_png)

    @property
    def is_group(self):
        """True when the whole store goes to one place, however it gets there."""
        return self.fulfillment_mode in self.GROUP_MODES

    @property
    def buyer_delivery_fee(self):
        """What each buyer pays for delivery on top of their items.

        Group ship only: one consignment still has to be paid for, and staff set that share
        from a ShipStation quote. Group delivery is a single local drop-off billed to the
        organization, so buyers are charged nothing; individual ship absorbs postage into the
        item prices until live rates land.
        """
        if self.fulfillment_mode == self.Fulfillment.GROUP_SHIP and self.group_ship_fee:
            # Coerced: an unsaved instance can still be holding whatever was assigned to it,
            # and this gets added to cart totals.
            return Decimal(str(self.group_ship_fee))
        return Decimal("0")

    @property
    def organization_delivery_fee(self):
        """What the organization is billed for the drop-off, or None if there's nothing to bill."""
        if self.fulfillment_mode != self.Fulfillment.GROUP_DELIVERY:
            return None
        fee = self.group_delivery_fee
        return Decimal(str(settings.DEFAULT_GROUP_DELIVERY_FEE)) if fee is None else fee

    @property
    def arrival(self):
        """The date range buyers are promised. None until the store has a close date."""
        from .arrival import estimated_arrival

        return estimated_arrival(self)

    @property
    def zone(self):
        return ZoneInfo(self.time_zone)

    # For templates: the times in the store's zone. The date filter leaves these alone instead
    # of converting them to the site zone, so "g:i a T" shows e.g. "6:00 p.m. PDT".
    @property
    def opens_local(self):
        return do_timezone(self.opens_at, self.zone) if self.opens_at else None

    @property
    def closes_local(self):
        return do_timezone(self.closes_at, self.zone) if self.closes_at else None


class StoreStatusChange(models.Model):
    """One entry in a store's status history: who (or the schedule) changed what, when."""

    class Reason(models.TextChoices):
        SCHEDULED = "scheduled", "On schedule"
        MANUAL = "manual", "Changed by staff"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="status_changes")
    from_status = models.CharField(
        max_length=20, choices=Store.Status.choices, blank=True,
        help_text="Status before the change. Blank when the store was just created.",
    )
    to_status = models.CharField(max_length=20, choices=Store.Status.choices)
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="store_status_changes",
        help_text="Staff member who made the change. Blank if the schedule did it.",
    )
    reason = models.CharField(
        max_length=20, choices=Reason.choices,
        help_text="Scheduled: opened/closed by lifecycle_tick. Manual: set in the store form.",
    )

    class Meta:
        ordering = ["changed_at", "pk"]

    def __str__(self):
        return f"{self.store}: {self.from_status or 'new'} -> {self.to_status} ({self.reason})"
