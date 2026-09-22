import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .cart import Cart
from .models import Order, mark_paid
from .views import _my_order

stripe.api_key = settings.STRIPE_SECRET_KEY


def _cents(amount):
    return int(round(amount * 100))


def order_pay(request, order_number):
    """Create a Stripe Checkout Session for the order and send the buyer to it."""
    order = _my_order(request, order_number)
    if order is None:
        return redirect("cart")
    if order.status != Order.Status.PENDING:
        return redirect("order_detail", order_number=order.order_number)

    base = f"{request.scheme}://{request.get_host()}"
    line_items = [
        {
            "quantity": item.quantity,
            "price_data": {
                "currency": "usd",
                "unit_amount": _cents(item.unit_price),
                "product_data": {"name": f"{item.product_name} {item.variant_label}".strip()},
            },
        }
        for item in order.items.all()
    ]
    # Group ship only: the consignment to the organization still costs money. A group-delivery
    # drop-off is billed to the organization, so it never reaches a buyer's card.
    if order.delivery_fee:
        line_items.append({
            "quantity": 1,
            "price_data": {
                "currency": "usd",
                "unit_amount": _cents(order.delivery_fee),
                "product_data": {"name": "Delivery"},
            },
        })
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=order.buyer_email,
        client_reference_id=order.order_number,
        metadata={"order_number": order.order_number},
        line_items=line_items,
        success_url=base + reverse("order_success", args=[order.order_number]) + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=base + reverse("cart"),
    )
    order.stripe_checkout_session = session.id
    order.save(update_fields=["stripe_checkout_session", "updated_at"])
    return redirect(session.url, permanent=False)


def order_success(request, order_number):
    """Buyer lands here after paying. Confirms with Stripe, marks paid, clears the cart."""
    order = _my_order(request, order_number)
    if order is None:
        return redirect("cart")
    session_id = request.GET.get("session_id") or order.stripe_checkout_session
    if order.status == Order.Status.PENDING and session_id:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status == "paid":
            mark_paid(order, session.payment_intent)
    if order.status != Order.Status.PENDING:
        Cart(request).clear()
    return render(request, "orders/order_success.html", {"order": order, "store": order.store, "brand": order.store.client})


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Stripe's server-to-server confirmation; the authoritative 'paid' signal."""
    try:
        event = stripe.Webhook.construct_event(
            request.body, request.META.get("HTTP_STRIPE_SIGNATURE", ""), settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponseBadRequest("invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        if session.get("payment_status") == "paid":
            order_number = (session.get("metadata") or {}).get("order_number")
            order = Order.objects.filter(order_number=order_number).first()
            if order:
                mark_paid(order, session.get("payment_intent") or "")
    return HttpResponse("ok")
