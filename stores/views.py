from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Store


def index(request):
    stores = Store.objects.filter(status__in=[Store.Status.OPEN, Store.Status.SCHEDULED]).select_related("client")
    return render(request, "stores/index.html", {"stores": stores})


def store_detail(request, slug):
    store = get_object_or_404(Store.objects.select_related("client"), slug=slug)
    if store.status == Store.Status.DRAFT and not request.user.is_staff:
        return render(request, "stores/closed.html", {"store": store}, status=404)
    offerings = (
        store.offerings.filter(is_active=True, product__is_active=True)
        .select_related("product")
        .prefetch_related("product__variants")
    )
    return render(
        request,
        "stores/store_detail.html",
        {"store": store, "offerings": offerings, "is_open": store.status == Store.Status.OPEN, "now": timezone.now()},
    )
