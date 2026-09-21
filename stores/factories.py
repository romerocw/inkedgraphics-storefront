"""Builders used by the test suite. Keeps test setup short and consistent across apps."""

import itertools
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from catalog.models import Product, ProductVariant, StoreProduct
from orders.models import Order, OrderItem
from stores.models import Client, Store

# Keeps default names, slugs and SKU prefixes unique across a test.
counter = itertools.count(1)


def make_staff(username="staffer", password="test-pass-1827"):
    return get_user_model().objects.create_user(username, password=password, is_staff=True)


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
        OrderItem.objects.create(
            order=order, store_product=offering, variant=offering.product.variants.first(), quantity=quantity
        )
    if items:
        order.recalculate()
    return order
