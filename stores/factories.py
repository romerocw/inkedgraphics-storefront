"""Builders used by the test suite. Keeps test setup short and consistent across apps."""

import itertools
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from catalog.models import Product, ProductVariant, StoreProduct
from orders.models import Order, OrderItem
from stores.models import Client, Store

# Keeps default names, slugs and SKU prefixes unique across a test.
counter = itertools.count(1)


def make_staff(username=None, password="test-pass-1827", role=None, **kwargs):
    """A staff user with a profile. role defaults to the profile default (staff)."""
    from console.models import StaffProfile, profile_for

    n = next(counter)
    kwargs.setdefault("email", f"staff{n}@inkedgraphics.com")
    user = get_user_model().objects.create_user(username or f"staffer{n}", password=password, is_staff=True, **kwargs)
    profile = profile_for(user)
    if role and profile.role != role:
        StaffProfile.objects.filter(pk=profile.pk).update(role=role)
        profile.refresh_from_db()
    return user


def make_owner(**kwargs):
    from console.models import StaffProfile

    return make_staff(role=StaffProfile.Role.OWNER, **kwargs)


def make_manager(**kwargs):
    from console.models import StaffProfile

    return make_staff(role=StaffProfile.Role.MANAGER, **kwargs)


def make_client(name=None, **kwargs):
    n = next(counter)
    name = name or f"Langley High {n}"
    kwargs.setdefault("slug", f"client-{n}")
    return Client.objects.create(name=name, **kwargs)


def make_store(client=None, name=None, **kwargs):
    n = next(counter)
    kwargs.setdefault("slug", f"store-{n}")
    kwargs.setdefault("status", Store.Status.OPEN)
    return Store.objects.create(client=client or make_client(), name=name or f"Spring Store {n}", **kwargs)


def make_group_store(mode=Store.Fulfillment.GROUP_DELIVERY, **kwargs):
    """A store whose whole order goes to one place, with somewhere for it to go."""
    kwargs.setdefault("delivery_location_name", "Langley High front office")
    kwargs.setdefault("delivery_address", "6520 Georgetown Pike\nMcLean, VA 22101")
    return make_store(fulfillment_mode=mode, **kwargs)


def make_product(name=None, sku_prefix=None, default_price="45.00", variants=(("Black", "M"),), **kwargs):
    n = next(counter)
    name = name or f"Unisex Hoodie {n}"
    sku_prefix = sku_prefix or f"HOOD-{n}"
    product = Product.objects.create(name=name, sku_prefix=sku_prefix, default_price=Decimal(default_price), **kwargs)
    for i, (color, size) in enumerate(variants):
        ProductVariant.objects.create(
            product=product, color=color, size=size, sku=f"{sku_prefix}-{color}-{size}".upper(), sort_order=i
        )
    return product


def make_blank(style_id=None, merch_label=None, sizes=("M", "2XL"), color="Black", **kwargs):
    """A style as if it had come from the ops sync, with a variant per size."""
    from catalog.models import Blank, BlankVariant

    n = style_id or next(counter)
    kwargs.setdefault("supplier_style_code", f"ST{n}")
    kwargs.setdefault("brand", "Comfort Colors")
    kwargs.setdefault("display_title", "SUPPLIER COPY — never shown to buyers")
    kwargs.setdefault("base_cost", Decimal("12.00"))
    kwargs.setdefault("ops_updated_at", timezone.now())
    blank = Blank.objects.create(style_id=n, merch_label=merch_label or "", **kwargs)
    for i, size in enumerate(sizes):
        BlankVariant.objects.create(
            blank=blank, blank_sku=f"{blank.supplier_style_code}-{color}-{size}",
            color_name=color, size=size, size_sort_order=i,
        )
    return blank


def make_blank_offering(store, blank=None, price="40.00", **kwargs):
    """What a store sells, from a synced blank."""
    return StoreProduct.objects.create(
        store=store, blank=blank or make_blank(), price=Decimal(price), **kwargs
    )


def make_offering(store, product=None, price=None, **kwargs):
    product = product or make_product()
    price = Decimal(price) if price is not None else product.default_price
    return StoreProduct.objects.create(store=store, product=product, price=price, **kwargs)


def make_order(store, status=Order.Status.PAID, items=(), **kwargs):
    n = next(counter)
    kwargs.setdefault("buyer_name", f"Buyer {n}")
    kwargs.setdefault("buyer_email", f"buyer{n}@example.com")
    if status != Order.Status.PENDING:
        kwargs.setdefault("paid_at", timezone.now())
    order = Order.objects.create(store=store, status=status, **kwargs)
    for offering, quantity in items:
        variant = offering.variants().first()
        OrderItem.objects.create(
            order=order, store_product=offering, quantity=quantity,
            blank_variant=variant if offering.blank_id else None,
            variant=None if offering.blank_id else variant,
        )
    if items:
        order.recalculate()
    return order


class TempMediaMixin:
    """For tests that write files: uploads go to a throwaway MEDIA_ROOT.

    Share kits are real PNGs and PDFs. Without this they pile up in the project's own media
    directory, one set per test run, and never get cleaned up.
    """

    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media_root = Path(media.name)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))


class TempRunDirMixin:
    """For tests that run cron commands: heartbeat files go to a throwaway RUN_DIR."""

    def setUp(self):
        super().setUp()
        run_dir = tempfile.TemporaryDirectory()
        self.addCleanup(run_dir.cleanup)
        self.run_dir = Path(run_dir.name)
        self.enterContext(override_settings(RUN_DIR=run_dir.name))
