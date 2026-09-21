from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView, View

from catalog.models import Product, StoreProduct
from orders.models import Order
from stores.models import Client, Store

from .forms import AddStoreProductsForm, ClientForm, ConsolePasswordChangeForm, StoreForm, StoreProductFormSet

# The statuses that mean money actually came in.
PAID_STATUSES = [Order.Status.PAID, Order.Status.SENT_TO_OPS, Order.Status.FULFILLED]
PAID = Q(orders__status__in=PAID_STATUSES)


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

    tabs = [("summary", "Summary"), ("products", "Products")]

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
