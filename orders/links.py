"""Links to an order that work outside the browser that placed it (e.g. from an email).

The buyer's order page normally only opens in the session that checked out. An emailed
link carries a signature of the order number instead; a valid one lets that browser in.
"""

from django.conf import settings
from django.core.signing import Signer
from django.urls import reverse
from django.utils.crypto import constant_time_compare

_signer = Signer(salt="orders.order-link")


def order_link_key(order_number):
    return _signer.signature(order_number)


def is_valid_key(order_number, key):
    return bool(key) and constant_time_compare(key, order_link_key(order_number))


def order_url(order):
    """Absolute link to the buyer's order page that opens in any browser."""
    path = reverse("order_detail", args=[order.order_number])
    return f"{settings.SITE_URL}{path}?k={order_link_key(order.order_number)}"
