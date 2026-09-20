from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count, Q, Sum
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

from orders.models import Order
from stores.models import Client, Store

from .forms import ClientForm, StoreForm

PAID = Q(orders__status__in=[Order.Status.PAID, Order.Status.SENT_TO_OPS, Order.Status.FULFILLED])


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = reverse_lazy("console:login")

    def test_func(self):
        return self.request.user.is_staff


class ConsoleLoginView(LoginView):
    template_name = "console/login.html"
    redirect_authenticated_user = True


class ConsoleLogoutView(LogoutView):
    next_page = reverse_lazy("console:login")


class DashboardView(StaffRequiredMixin, TemplateView):
    template_name = "console/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        paid = Order.objects.filter(status__in=[Order.Status.PAID, Order.Status.SENT_TO_OPS, Order.Status.FULFILLED])
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


class StoreCreateView(StaffRequiredMixin, CreateView):
    model, form_class = Store, StoreForm
    template_name = "console/form.html"
    success_url = reverse_lazy("console:stores")
    extra_context = {"title": "New store"}


class StoreUpdateView(StaffRequiredMixin, UpdateView):
    model, form_class = Store, StoreForm
    template_name = "console/form.html"
    success_url = reverse_lazy("console:stores")
    extra_context = {"title": "Edit store"}
