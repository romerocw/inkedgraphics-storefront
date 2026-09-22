from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import BlankVariant, ProductVariant, StoreProduct
from stores.arrival import estimated_arrival
from stores.models import Store

from .cart import Cart
from .forms import CheckoutForm
from .links import is_valid_key
from .models import Order, OrderItem


@require_POST
def cart_add(request, slug):
    store = get_object_or_404(Store, slug=slug, status=Store.Status.OPEN)
    sp = get_object_or_404(StoreProduct, id=request.POST.get("store_product"), store=store, is_active=True)
    if sp.blank_id:
        variant = get_object_or_404(BlankVariant, id=request.POST.get("variant"), blank=sp.blank, is_active=True)
    else:
        variant = get_object_or_404(ProductVariant, id=request.POST.get("variant"), product=sp.product, is_active=True)
    try:
        qty = max(1, int(request.POST.get("quantity", 1)))
    except ValueError:
        qty = 1
    Cart(request).add(sp, variant, qty, separate_line=store.is_group)
    messages.success(request, f"Added {qty} × {sp.name} ({variant.color} {variant.size}) to your cart.")
    return redirect("cart")


def cart_view(request):
    cart = Cart(request)
    store = Store.objects.filter(id=cart.store_id).first() if cart.store_id else None
    fee = store.buyer_delivery_fee if store else 0
    return render(request, "orders/cart.html", {
        "cart": cart, "items": list(cart.items()), "store": store,
        "brand": store.client if store else None,
        "delivery_fee": fee, "order_total": cart.total() + fee,
    })


@require_POST
def cart_update(request):
    cart = Cart(request)
    for key, value in request.POST.items():
        if key.startswith("label:"):
            cart.set_label(key[6:], value)
    # Labels first: setting a quantity to zero drops the line, and there's no point recording
    # who a line was for after it's gone.
    for key, value in request.POST.items():
        if key.startswith("qty:"):
            try:
                cart.set_qty(key[4:], int(value or 0))
            except ValueError:
                pass
    # The cart is one form, so "Checkout" saves the labels the buyer just typed on the way.
    return redirect("checkout" if "checkout" in request.POST else "cart")


def checkout(request):
    cart = Cart(request)
    items = list(cart.items())
    if not items:
        return redirect("cart")
    store = get_object_or_404(Store, id=cart.store_id, status=Store.Status.OPEN)
    if store.is_group and any(not i["label"] for i in items):
        # The pack-out list is grouped by these, so an unlabelled line can't be sorted on the floor.
        messages.error(request, "Please say who each item is for before checking out.")
        return redirect("cart")
    form = CheckoutForm(request.POST or None, store=store)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            order = form.save(commit=False)
            order.store = store
            order.delivery_fee = store.buyer_delivery_fee
            # Snapshot the promise this buyer was just shown; the store's own dates move on.
            arrival = estimated_arrival(store)
            if arrival:
                order.promised_arrival_earliest, order.promised_arrival_latest = arrival
            order.save()
            for i in items:
                variant = i["variant"]
                OrderItem.objects.create(
                    order=order, store_product=i["store_product"],
                    blank_variant=variant if isinstance(variant, BlankVariant) else None,
                    variant=None if isinstance(variant, BlankVariant) else variant,
                    quantity=i["qty"], unit_price=i["unit_price"], recipient_label=i["label"],
                )
            order.recalculate()
        request.session.setdefault("my_orders", []).append(order.order_number)
        request.session.modified = True
        return redirect("order_pay", order_number=order.order_number)
    fee = store.buyer_delivery_fee
    return render(request, "orders/checkout.html", {
        "form": form, "items": items, "cart": cart, "store": store, "brand": store.client,
        "delivery_fee": fee, "order_total": cart.total() + fee,
    })


def _my_order(request, order_number):
    """The order, if this browser placed it, was sent a link to it, or is staff; else None."""
    if is_valid_key(order_number, request.GET.get("k")) and order_number not in request.session.get("my_orders", []):
        request.session.setdefault("my_orders", []).append(order_number)
        request.session.modified = True
    if order_number not in request.session.get("my_orders", []) and not request.user.is_staff:
        return None
    return get_object_or_404(Order.objects.select_related("store"), order_number=order_number)



def order_detail(request, order_number):
    order = _my_order(request, order_number)
    if order is None:
        return redirect("cart")
    return render(request, "orders/order_detail.html", {"order": order, "store": order.store, "brand": order.store.client})
