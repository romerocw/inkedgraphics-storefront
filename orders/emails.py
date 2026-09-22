"""Email to buyers about their orders. Queued in the outbox, sent by cron."""

from messaging.outbox import enqueue

from .links import order_url

ORDER_CONFIRMATION = "order_confirmation"


def queue_order_confirmation(order):
    """Queue the 'thanks, here's your order' email to the buyer. Returns the outbox row."""
    store = order.store
    client = store.client
    return enqueue(
        ORDER_CONFIRMATION,
        (order.buyer_name, order.buyer_email),
        f"Order {order.order_number} confirmed — {store.name}",
        "orders/email/order_confirmation",
        {
            "order": order,
            "items": list(order.items.all()),
            "store": store,
            "brand_name": client.name,
            "brand_color": store.primary_color or client.primary_color,
            # What the buyer was told at checkout, not what the store would compute now: this
            # email is sent from cron, often after the store has closed.
            "arrival": order.promised_arrival,
            "order_url": order_url(order),
        },
        related=order,
    )
