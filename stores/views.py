from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Store


def index(request):
    stores = Store.objects.filter(status__in=[Store.Status.OPEN, Store.Status.SCHEDULED]).select_related("client")
    return render(request, "stores/index.html", {"stores": stores})


def share_image(request, slug):
    """The social card image, at a stable public URL.

    Media URLs are signed and expire within the hour, but a link pasted into a group chat gets
    re-scraped days later, so the Open Graph image can't be one of those. Serving it here keeps
    the bucket private and the URL permanent.
    """
    store = get_object_or_404(Store, slug=slug)
    if not store.share_social_png:
        raise Http404("This store has no share image yet.")
    response = FileResponse(store.share_social_png.open("rb"), content_type="image/png")
    response["Cache-Control"] = "public, max-age=3600"
    return response


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
        {
            "store": store, "brand": store.client, "offerings": offerings,
            "is_open": store.status == Store.Status.OPEN, "now": timezone.now(),
        },
    )
