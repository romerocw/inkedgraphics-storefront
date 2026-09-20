from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog.models import ProductVariant, StoreProduct
from stores.models import Store

from .cart import Cart
from .forms import CheckoutForm
from .models import Order, OrderItem


@require_POST
def cart_add(request, slug):
    store = get_object_or_404(Store, slug=slug, status=Store.Status.OPEN)
    sp = get_object_or_404(StoreProduct, id=request.POST.get("store_product"), store=store, is_active=True)
    variant = get_object_or_404(ProductVariant, id=request.POST.get("variant"), product=sp.product, is_active=True)
    try:
        qty = max(1, int(request.POST.get("quantity", 1)))
    except ValueError:
        qty = 1
    Cart(request).add(sp, variant, qty)
    messages.success(request, f"Added {qty} × {sp.name} ({variant.color} {variant.size}) to your cart.")
    return redirect("cart")


def cart_view(request):
    cart = Cart(request)
    store = Store.objects.filter(id=cart.store_id).first() if cart.store_id else None
    return render(request, "orders/cart.html", {"cart": cart, "items": list(cart.items()), "store": store, "brand": store.client if store else None})


@require_POST
def cart_update(request):
    cart = Cart(request)
    for key, value in request.POST.items():
        if key.startswith("qty:"):
            try:
                cart.set_qty(key[4:], int(value or 0))
            except ValueError:
                pass
    return redirect("cart")


def checkout(request):
    cart = Cart(request)
    items = list(cart.items())
    if not items:
        return redirect("cart")
    store = get_object_or_404(Store, id=cart.store_id, status=Store.Status.OPEN)
    form = CheckoutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            order = form.save(commit=False)
            order.store = store
            order.save()
            for i in items:
                OrderItem.objects.create(
                    order=order, store_product=i["store_product"], variant=i["variant"],
                    quantity=i["qty"], unit_price=i["unit_price"],
                )
            order.recalculate()
        cart.clear()
        request.session.setdefault("my_orders", []).append(order.order_number)
        request.session.modified = True
        return redirect("order_pay", order_number=order.order_number)
    return render(request, "orders/checkout.html", {"form": form, "items": items, "cart": cart, "store": store, "brand": store.client})


def _my_order(request, order_number):
    if order_number not in request.session.get("my_orders", []) and not request.user.is_staff:
        return None
    return get_object_or_404(Order.objects.select_related("store"), order_number=order_number)


def order_pay(request, order_number):
    """Placeholder until Stripe Checkout is wired in."""
    order = _my_order(request, order_number)
    if order is None:
        return redirect("cart")
    return render(request, "orders/order_pay.html", {"order": order, "store": order.store, "brand": order.store.client})


def order_detail(request, order_number):
    order = _my_order(request, order_number)
    if order is None:
        return redirect("cart")
    return render(request, "orders/order_detail.html", {"order": order, "store": order.store, "brand": order.store.client})
