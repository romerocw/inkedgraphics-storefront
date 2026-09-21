import csv
import io
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView, View

from catalog.models import Product, StoreProduct
from orders.models import Order, allowed_transitions, change_status
from stores.models import Client, Store

from .forms import (
    AddStoreProductsForm,
    ClientForm,
    ConsolePasswordChangeForm,
    OrderFilterForm,
    OrderStatusForm,
    ProductFilterForm,
    ProductForm,
    ProductVariantFormSet,
    StoreForm,
    StoreProductFormSet,
)

# The statuses that mean money actually came in.
PAID_STATUSES = [Order.Status.PAID, Order.Status.SENT_TO_OPS, Order.Status.FULFILLED]
PAID = Q(orders__status__in=PAID_STATUSES)
ORDERS_PER_PAGE = 50


def filter_orders(queryset, params):
    """Apply the filter bar to an order queryset. Used by both order lists and the CSV."""
    status = params.get("status") or ""
    if status == OrderFilterForm.ALL:
        pass
    elif status in Order.Status.values:
        queryset = queryset.filter(status=status)
    else:
        queryset = queryset.filter(status__in=PAID_STATUSES)

    search = (params.get("q") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(order_number__icontains=search)
            | Q(buyer_name__icontains=search)
            | Q(buyer_email__icontains=search)
            | Q(recipient_name__icontains=search)
        )

    store = params.get("store") or ""
    if store.isdigit():
        queryset = queryset.filter(store_id=store)

    # Pending orders have no paid date, so sort on "whichever date we have".
    queryset = queryset.annotate(order_date=Coalesce("paid_at", "created_at"))
    return queryset.order_by("order_date" if params.get("sort") == "oldest" else "-order_date")


def order_list_context(request, queryset, *, with_store, bulk_next, extra_params=None):
    """Filter bar + sorting + pagination, shared by /console/orders/ and a store's Orders tab."""
    params = request.GET
    orders = filter_orders(queryset, params).select_related("store")
    page = Paginator(orders, ORDERS_PER_PAGE).get_page(params.get("page"))

    keep = {k: v for k, v in params.items() if k not in ("page", "sort") and v}
    keep.update(extra_params or {})
    sort = "newest" if params.get("sort") == "oldest" else "oldest"
    return {
        "filter_form": OrderFilterForm(params or None, with_store=with_store),
        "page_obj": page,
        "orders": page.object_list,
        "show_store": with_store,
        "bulk_next": bulk_next,
        "sort": params.get("sort") or "newest",
        "sort_query": urlencode({**keep, "sort": sort}),
        "page_query": urlencode({**keep, **({"sort": params["sort"]} if params.get("sort") else {})}),
    }


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = reverse_lazy("console:login")

    def test_func(self):
        return self.request.user.is_staff


class ConsoleLoginView(LoginView):
    template_name = "console/login.html"
    redirect_authenticated_user = True


class ConsoleLogoutView(LogoutView):
    next_page = reverse_lazy("console:login")


class ConsolePasswordChangeView(StaffRequiredMixin, SuccessMessageMixin, PasswordChangeView):
    form_class = ConsolePasswordChangeForm
    template_name = "console/password_change.html"
    success_url = reverse_lazy("console:dashboard")
    success_message = "Your password has been changed."


class DashboardView(StaffRequiredMixin, TemplateView):
    template_name = "console/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        paid = Order.objects.filter(status__in=PAID_STATUSES)
        week_ago = timezone.now() - timedelta(days=7)
        ctx.update(
            clients=Client.objects.count(),
            open_stores=Store.objects.filter(status=Store.Status.OPEN).count(),
            orders_total=paid.count(),
            orders_week=paid.filter(paid_at__gte=week_ago).count(),
            revenue_total=paid.aggregate(s=Sum("total"))["s"] or 0,
            revenue_week=paid.filter(paid_at__gte=week_ago).aggregate(s=Sum("total"))["s"] or 0,
            recent_orders=paid.select_related("store")[:10],
            closing_soon=Store.objects.filter(status=Store.Status.OPEN, closes_at__lte=timezone.now() + timedelta(days=7)).select_related("client")[:10],
        )
        return ctx


class ClientListView(StaffRequiredMixin, ListView):
    template_name = "console/client_list.html"
    queryset = Client.objects.annotate(store_count=Count("stores", distinct=True))


class ClientCreateView(StaffRequiredMixin, CreateView):
    model, form_class = Client, ClientForm
    template_name = "console/form.html"
    success_url = reverse_lazy("console:clients")
    extra_context = {"title": "New client"}


class ClientUpdateView(StaffRequiredMixin, UpdateView):
    model, form_class = Client, ClientForm
    template_name = "console/form.html"
    success_url = reverse_lazy("console:clients")
    extra_context = {"title": "Edit client"}


class StoreListView(StaffRequiredMixin, ListView):
    template_name = "console/store_list.html"
    queryset = (
        Store.objects.select_related("client")
        .annotate(paid_orders=Count("orders", filter=PAID, distinct=True), revenue=Sum("orders__total", filter=PAID))
    )


class StoreDetailMixin(StaffRequiredMixin):
    """Shared plumbing for the store page and the POST endpoints that render it again."""

    tabs = [("summary", "Summary"), ("products", "Products"), ("orders", "Orders")]

    def get_store(self):
        return get_object_or_404(Store.objects.select_related("client"), pk=self.kwargs["pk"])

    def tab_context(self, store, tab, formset=None, add_form=None):
        paid = store.orders.filter(status__in=PAID_STATUSES)
        ctx = {
            "store": store,
            "tab": tab if tab in dict(self.tabs) else "summary",
            "tabs": self.tabs,
            "paid_orders": paid.count(),
            "revenue": paid.aggregate(s=Sum("total"))["s"] or 0,
        }
        if ctx["tab"] == "orders":
            ctx.update(
                order_list_context(
                    self.request,
                    store.orders.all(),
                    with_store=False,
                    bulk_next=self.tab_url(store, "orders"),
                    extra_params={"tab": "orders"},
                )
            )
        if ctx["tab"] == "products":
            offerings = store.offerings.select_related("product").order_by("sort_order", "pk")
            ctx["formset"] = formset if formset is not None else StoreProductFormSet(queryset=offerings)
            ctx["add_form"] = add_form if add_form is not None else AddStoreProductsForm(products=self.candidates(store))
        return ctx

    def candidates(self, store):
        """Active catalog products this store doesn't offer yet."""
        return Product.objects.filter(is_active=True).exclude(store_offerings__store=store)

    def render_tab(self, store, tab, **kwargs):
        return render(self.request, "console/store_detail.html", self.tab_context(store, tab, **kwargs))

    def tab_url(self, store, tab):
        return f"{reverse('console:store_detail', args=[store.pk])}?tab={tab}"


class StoreDetailView(StoreDetailMixin, DetailView):
    template_name = "console/store_detail.html"
    model = Store

    def get_context_data(self, **kwargs):
        return super().get_context_data(**self.tab_context(self.object, self.request.GET.get("tab", "summary")))


class StoreProductsView(StoreDetailMixin, View):
    """Saves the price/for-sale/order edits made inline on the Products tab."""

    def post(self, request, pk):
        store = self.get_store()
        offerings = store.offerings.select_related("product").order_by("sort_order", "pk")
        formset = StoreProductFormSet(request.POST, queryset=offerings)
        if not formset.is_valid():
            messages.error(request, "Those changes weren't saved — please check the highlighted rows.")
            return self.render_tab(store, "products", formset=formset)
        saved = formset.save()
        messages.success(request, f"Saved {len(saved)} product{'' if len(saved) == 1 else 's'}." if saved else "Nothing to save.")
        return redirect(self.tab_url(store, "products"))


class StoreProductsAddView(StoreDetailMixin, View):
    """Adds catalog products to a store, either the checked ones or all of them."""

    def post(self, request, pk):
        store = self.get_store()
        candidates = list(self.candidates(store))
        add_form = AddStoreProductsForm(request.POST, products=candidates)
        if not add_form.is_valid():
            messages.error(request, "Nothing was added — please check the prices below.")
            return self.render_tab(store, "products", add_form=add_form)

        next_sort = (store.offerings.aggregate(m=Max("sort_order"))["m"] or 0) + 1
        added = []
        for product, price in add_form.chosen(everything="add_all" in request.POST):
            StoreProduct.objects.create(store=store, product=product, price=price, sort_order=next_sort)
            next_sort += 1
            added.append(product.name)

        if added:
            messages.success(request, f"Added {len(added)} product{'' if len(added) == 1 else 's'} to {store.name}.")
        else:
            messages.error(request, "Tick the products you want to add, then press Add selected.")
        return redirect(self.tab_url(store, "products"))


class StoreCreateView(StaffRequiredMixin, CreateView):
    model, form_class = Store, StoreForm
    template_name = "console/form.html"
    success_url = reverse_lazy("console:stores")
    extra_context = {"title": "New store"}


class StoreUpdateView(StaffRequiredMixin, UpdateView):
    model, form_class = Store, StoreForm
    template_name = "console/form.html"
    extra_context = {"title": "Edit store"}

    def get_success_url(self):
        return reverse("console:store_detail", args=[self.object.pk])


CSV_COLUMNS = [
    "order_number", "paid_at", "buyer_name", "buyer_email", "buyer_phone", "recipient_name",
    "product_name", "variant_label", "sku", "quantity", "unit_price", "line_total", "order_total", "notes",
]


class StoreOrdersCSVView(StoreDetailMixin, View):
    """One row per item, in the column order the ops system is keyed from."""

    def get(self, request, pk):
        store = self.get_store()
        orders = filter_orders(store.orders.all(), request.GET).prefetch_related("items")

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_COLUMNS)
        for order in orders:
            paid_at = timezone.localtime(order.paid_at).strftime("%Y-%m-%d %H:%M") if order.paid_at else ""
            for item in order.items.all():
                writer.writerow([
                    order.order_number, paid_at, order.buyer_name, order.buyer_email, order.buyer_phone,
                    order.recipient_name, item.product_name, item.variant_label, item.sku, item.quantity,
                    f"{item.unit_price:.2f}", f"{item.line_total:.2f}", f"{order.total:.2f}", order.notes,
                ])

        # utf-8-sig: Excel needs the BOM to read accents correctly.
        response = HttpResponse(buffer.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{store.slug}-orders.csv"'
        return response


class OrderListView(StaffRequiredMixin, TemplateView):
    template_name = "console/order_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            order_list_context(
                self.request, Order.objects.all(), with_store=True, bulk_next=self.request.get_full_path()
            )
        )
        return ctx


class OrderDetailView(StaffRequiredMixin, DetailView):
    template_name = "console/order_detail.html"
    slug_field = slug_url_kwarg = "order_number"
    queryset = Order.objects.select_related("store", "store__client").prefetch_related("items", "status_changes__changed_by")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        moves = allowed_transitions(self.object)
        ctx.update(
            moves=[(status, Order.Status(status).label) for status in moves if status != Order.Status.CANCELLED],
            can_cancel=Order.Status.CANCELLED in moves,
        )
        return ctx


class OrderStatusView(StaffRequiredMixin, View):
    """One status move on one order. Amounts are never editable."""

    def post(self, request, order_number):
        order = get_object_or_404(Order, order_number=order_number)
        form = OrderStatusForm(request.POST)
        if form.is_valid():
            try:
                change_status(order, form.cleaned_data["to_status"], request.user, form.cleaned_data["note"])
                messages.success(request, f"{order.order_number} is now {order.get_status_display().lower()}.")
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
        else:
            messages.error(request, "That status change didn't make sense — please try again.")
        return redirect("console:order_detail", order_number=order.order_number)


class OrdersBulkView(StaffRequiredMixin, View):
    """Marks several paid orders as sent to production in one go."""

    def post(self, request):
        orders = Order.objects.filter(pk__in=request.POST.getlist("orders"))
        sent, skipped = 0, 0
        for order in orders:
            try:
                change_status(order, Order.Status.SENT_TO_OPS, request.user)
                sent += 1
            except ValidationError:
                skipped += 1

        if sent:
            messages.success(request, f"Marked {sent} order{'' if sent == 1 else 's'} sent to production.")
        if skipped:
            messages.error(request, f"Left {skipped} order{'' if skipped == 1 else 's'} alone — only paid orders can be sent to production.")
        if not orders:
            messages.error(request, "Tick the orders you want to mark, then press the button.")

        next_url = request.POST.get("next") or ""
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect("console:orders")


class ProductListView(StaffRequiredMixin, ListView):
    template_name = "console/product_list.html"

    def get_queryset(self):
        products = Product.objects.annotate(variant_count=Count("variants"))
        search = (self.request.GET.get("q") or "").strip()
        if search:
            products = products.filter(Q(name__icontains=search) | Q(sku_prefix__icontains=search))
        active = self.request.GET.get("active")
        if active in ("0", "1"):
            products = products.filter(is_active=active == "1")
        return products

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = ProductFilterForm(self.request.GET or None)
        return ctx


class ProductFormMixin(StaffRequiredMixin):
    """Product form plus its size/color rows, saved together."""

    model, form_class = Product, ProductForm
    template_name = "console/product_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "formset" not in ctx:
            product = self.object
            ctx["formset"] = ProductVariantFormSet(
                instance=product, form_kwargs={"sku_prefix": product.sku_prefix if product else ""}
            )
        return ctx

    def form_valid(self, form):
        formset = ProductVariantFormSet(
            self.request.POST, instance=form.instance, form_kwargs={"sku_prefix": form.cleaned_data["sku_prefix"]}
        )
        if not formset.is_valid():
            messages.error(self.request, "Nothing was saved — please check the sizes and colors below.")
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        with transaction.atomic():
            self.object = form.save()
            formset.instance = self.object
            formset.save()
        messages.success(self.request, f"Saved {self.object.name}.")
        return redirect("console:product_edit", pk=self.object.pk)


class ProductCreateView(ProductFormMixin, CreateView):
    extra_context = {"title": "New product"}


class ProductUpdateView(ProductFormMixin, UpdateView):
    extra_context = {"title": "Edit product"}
