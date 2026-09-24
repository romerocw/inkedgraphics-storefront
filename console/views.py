import csv
import io
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login as sign_in
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView, View

from catalog.models import Blank, Product, StoreProduct
from messaging.models import OutboxEmail
from config import heartbeat
from messaging.outbox import due, emails_about, retry
from orders.emails import ORDER_CONFIRMATION, queue_order_confirmation
from orders.models import Order, OrderItem, allowed_transitions, change_status
from stores.lifecycle import record_manual_change, schedule_notes
from stores.models import Client, Store
from integrations.catalog_sync import sync_catalog
from integrations.models import CatalogSyncState
from integrations.ops_client import OpsClient, OpsError
from stores.services import share_kit

from .invitations import InvitationInvalid, send_invitation, user_from_token
from .mail import site_url
from .models import StaffProfile, profile_for
from .permissions import can_edit, can_manage_team, can_set_active
from .forms import (
    PickBlankForm,
    ClientForm,
    ConsoleAuthenticationForm,
    ConsolePasswordChangeForm,
    ConsolePasswordResetForm,
    ConsoleSetPasswordForm,
    MyAccountForm,
    OrderFilterForm,
    OrderStatusForm,
    ProductFilterForm,
    ProductForm,
    ProductVariantFormSet,
    StoreForm,
    StoreProductFormSet,
    TeamInviteForm,
    TeamMemberForm,
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
    authentication_form = ConsoleAuthenticationForm
    redirect_authenticated_user = True


class ConsoleLogoutView(LogoutView):
    next_page = reverse_lazy("console:login")

    def post(self, request, *args, **kwargs):
        # Signing out clears the session, so the message is added to the fresh one.
        response = super().post(request, *args, **kwargs)
        messages.success(request, "You've been signed out.")
        return response


class ConsolePasswordResetView(PasswordResetView):
    """Step 1 of forgotten-password: ask for an email address."""

    template_name = "console/password_reset.html"
    form_class = ConsolePasswordResetForm
    subject_template_name = "console/email/password_reset_subject.txt"
    email_template_name = "console/email/password_reset.txt"
    html_email_template_name = "console/email/password_reset.html"
    success_url = reverse_lazy("console:password_reset_sent")

    @property
    def extra_email_context(self):
        return {"site_url": site_url(self.request)}


class ConsolePasswordResetSentView(PasswordResetDoneView):
    """Step 2: told to check their email, whether or not the address existed."""

    template_name = "console/password_reset_sent.html"


class ConsolePasswordResetConfirmView(PasswordResetConfirmView):
    """Step 3: set a new password from the emailed link."""

    template_name = "console/password_reset_confirm.html"
    form_class = ConsoleSetPasswordForm
    success_url = reverse_lazy("console:password_reset_done")


class ConsolePasswordResetDoneView(PasswordResetCompleteView):
    """Step 4: done, go and sign in."""

    template_name = "console/password_reset_done.html"


class MyAccountView(StaffRequiredMixin, SuccessMessageMixin, UpdateView):
    """Everyone's own details. The password lives on its own page."""

    form_class = MyAccountForm
    template_name = "console/my_account.html"
    success_url = reverse_lazy("console:my_account")
    success_message = "Your details have been saved."

    def get_object(self, queryset=None):
        return self.request.user

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["profile"] = profile_for(self.request.user)
        return ctx


class ConsolePasswordChangeView(StaffRequiredMixin, SuccessMessageMixin, PasswordChangeView):
    form_class = ConsolePasswordChangeForm
    template_name = "console/password_change.html"
    success_url = reverse_lazy("console:my_account")
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
            revenue_total=paid.aggregate(s=Sum("subtotal"))["s"] or 0,
            revenue_week=paid.filter(paid_at__gte=week_ago).aggregate(s=Sum("subtotal"))["s"] or 0,
            recent_orders=paid.select_related("store")[:10],
            closing_soon=Store.objects.filter(status=Store.Status.OPEN, closes_at__lte=timezone.now() + timedelta(days=7)).select_related("client")[:10],
        )
        if can_manage_team(self.request.user):
            ctx["system"] = system_status()
        return ctx


# A cron job that hasn't beaten for this long is shown in red on the dashboard.
STALE_AFTER = timedelta(minutes=15)
CRON_JOBS = [
    ("lifecycle_tick", "Store schedule", "every 5 minutes"),
    ("send_outbox", "Email sending", "every minute"),
]


def system_status():
    """When each cron job last ran (from its heartbeat file), plus the email backlog."""
    now = timezone.now()
    jobs = []
    for name, label, cadence in CRON_JOBS:
        last = heartbeat.last_beat(name)
        jobs.append({"name": name, "label": label, "cadence": cadence, "last": last, "stale": last is None or now - last > STALE_AFTER})
    # An open store whose kit never built is a store nobody can scan their way into, and the
    # count in the cron summary line scrolls away. Show the stores themselves.
    kits_failing = Store.objects.filter(status=Store.Status.OPEN).exclude(share_kit_error="").select_related("client")
    return {
        "jobs": jobs,
        "emails_waiting": due().count(),
        "emails_given_up": OutboxEmail.objects.filter(status=OutboxEmail.Status.FAILED, next_attempt_at=None).count(),
        "kits_failing": list(kits_failing[:3]),
        "kits_failing_count": kits_failing.count(),
    }


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
        .annotate(paid_orders=Count("orders", filter=PAID, distinct=True), revenue=Sum("orders__subtotal", filter=PAID))
    )


UNNAMED_RECIPIENT = "Not named"


def packout_groups(store):
    """Every paid item in the store, gathered under the person it's for, for the floor.

    Orders placed before recipient labels existed have none, so they collect under one
    "Not named" heading at the end rather than vanishing from the sort.
    """
    items = (
        OrderItem.objects
        .filter(order__store=store, order__status__in=PAID_STATUSES)
        .select_related("order")
        .order_by("product_name", "variant_label")
    )
    groups = {}
    for item in items:
        groups.setdefault(item.recipient_label.strip() or UNNAMED_RECIPIENT, []).append(item)
    return [
        {"recipient": name, "items": lines, "pieces": sum(i.quantity for i in lines)}
        for name, lines in sorted(groups.items(), key=lambda kv: (kv[0] == UNNAMED_RECIPIENT, kv[0].lower()))
    ]


class StoreDetailMixin(StaffRequiredMixin):
    """Shared plumbing for the store page and the POST endpoints that render it again."""

    BASE_TABS = [("summary", "Summary"), ("products", "Products"), ("orders", "Orders")]

    def tabs_for(self, store):
        """Only a store that arrives in one consignment needs sorting on the floor."""
        tabs = list(self.BASE_TABS)
        if store.is_group:
            tabs.append(("packout", "Pack-out"))
        # Nothing to share before a store is open, and a closed store keeps its last kit.
        if store.status in (Store.Status.OPEN, Store.Status.CLOSED):
            tabs.append(("share", "Share kit"))
        return tabs

    def get_store(self):
        return get_object_or_404(Store.objects.select_related("client"), pk=self.kwargs["pk"])

    def tab_context(self, store, tab, formset=None, add_form=None):
        paid = store.orders.filter(status__in=PAID_STATUSES)
        tabs = self.tabs_for(store)
        ctx = {
            "store": store,
            "schedule": schedule_notes(store),
            "tab": tab if tab in dict(tabs) else "summary",
            "tabs": tabs,
            "paid_orders": paid.count(),
            "revenue": paid.aggregate(s=Sum("subtotal"))["s"] or 0,
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
        if ctx["tab"] == "packout":
            ctx["packout"] = packout_groups(store)
        if ctx["tab"] == "share":
            ctx["share_text"] = share_kit.share_text(store)
            ctx["share_url"] = share_kit.store_url(store)
            ctx["share_stale"] = store.share_kit_fingerprint != share_kit.fingerprint(store)
        if ctx["tab"] == "summary":
            ctx["status_history"] = store.status_changes.select_related("changed_by").order_by("-changed_at", "-pk")[:10]
        if ctx["tab"] == "products":
            offerings = store.offerings.select_related("product", "blank").order_by("sort_order", "pk")
            ctx["formset"] = formset if formset is not None else StoreProductFormSet(queryset=offerings)
            # ?style=<id> means a blank has been picked from the search: show its colours and
            # sizes so staff can say what this store actually sells.
            picked = self.picked_blank()
            ctx["picked_blank"] = picked
            if add_form is not None:
                ctx["add_form"] = add_form
                ctx["picked_blank"] = add_form.blank
            elif picked:
                ctx["add_form"] = PickBlankForm(blank=picked)
        return ctx

    def picked_blank(self):
        style = self.request.GET.get("style")
        if not (style and str(style).isdigit()):
            return None
        return Blank.objects.filter(pk=style, is_active=True).first()

    def render_tab(self, store, tab, **kwargs):
        return render(self.request, "console/store_detail.html", self.tab_context(store, tab, **kwargs))

    def tab_url(self, store, tab):
        return f"{reverse('console:store_detail', args=[store.pk])}?tab={tab}"


class StoreDetailView(StoreDetailMixin, DetailView):
    template_name = "console/store_detail.html"
    model = Store

    def get_context_data(self, **kwargs):
        return super().get_context_data(**self.tab_context(self.object, self.request.GET.get("tab", "summary")))


class CatalogListView(StaffRequiredMixin, ListView):
    """What the ops sync has pulled in. Read-only: ops owns these rows."""

    template_name = "console/catalog_list.html"
    paginate_by = 50

    def get_queryset(self):
        blanks = Blank.objects.prefetch_related("variants").annotate(
            used_by=Count("store_offerings", distinct=True)
        )
        search = (self.request.GET.get("q") or "").strip()
        if search:
            blanks = blanks.filter(
                Q(supplier_style_code__icontains=search)
                | Q(merch_label__icontains=search)
                | Q(brand__icontains=search)
                | Q(display_title__icontains=search)
                | Q(category__icontains=search)
            )
        if self.request.GET.get("active") == "0":
            blanks = blanks.filter(is_active=False)
        elif self.request.GET.get("active") != "all":
            blanks = blanks.filter(is_active=True)
        return blanks

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["sync"] = CatalogSyncState.load()
        ctx["query"] = self.request.GET.get("q", "")
        ctx["active"] = self.request.GET.get("active", "")
        ctx["total"] = Blank.objects.count()
        return ctx


class CatalogRefreshView(StaffRequiredMixin, View):
    """Pull the catalog now, rather than waiting for the next scheduled run."""

    def post(self, request):
        client = OpsClient()
        if not client.is_configured:
            messages.error(request, "The ops catalog API isn't configured on this server yet.")
            return redirect("console:catalog")
        try:
            result = sync_catalog(client=client, full=request.POST.get("full") == "1")
        except OpsError as exc:
            messages.error(request, f"The catalog couldn't be refreshed: {exc}")
        else:
            messages.success(
                request,
                f"Catalog refreshed — {result.styles} style{'' if result.styles == 1 else 's'} checked, "
                f"{result.created} new, {len(result.deactivated)} now inactive.",
            )
        return redirect("console:catalog")


class CatalogSearchView(StaffRequiredMixin, View):
    """Type-ahead over the synced catalog, for the store product picker."""

    LIMIT = 12

    @staticmethod
    def color_dots(blank):
        """One dot per colour, not per variant — a style has a row per colour and size."""
        seen = []
        for hex_value in blank.variants.filter(is_active=True).values_list("color_hex", flat=True):
            if hex_value and hex_value not in seen:
                seen.append(hex_value)
        return seen[:6]

    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        store_id = request.GET.get("store")
        blanks = Blank.objects.filter(is_active=True)
        if store_id and str(store_id).isdigit():
            blanks = blanks.exclude(store_offerings__store_id=store_id)
        if query:
            blanks = blanks.filter(
                Q(supplier_style_code__icontains=query)
                | Q(merch_label__icontains=query)
                | Q(brand__icontains=query)
                | Q(display_title__icontains=query)
                | Q(category__icontains=query)
            )
        rows = [
            {
                "id": blank.pk,
                "name": blank.buyer_name,
                # display_title is supplier copy: fine for staff searching, never for a buyer.
                "detail": " · ".join(p for p in (blank.supplier_style_code, blank.category) if p),
                "colors": self.color_dots(blank),
            }
            for blank in blanks.prefetch_related("variants")[:self.LIMIT]
        ]
        return JsonResponse({"results": rows})


class StoreShareKitView(StoreDetailMixin, View):
    """Rebuild a store's share kit on demand, for when staff want it now."""

    def post(self, request, pk):
        store = self.get_store()
        built, error = share_kit.try_generate(store, force=True)
        if error:
            messages.error(request, "That share kit couldn't be built — the reason is shown below.")
        else:
            messages.success(request, "Share kit rebuilt.")
        return redirect(self.tab_url(store, "share"))


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
    """Adds one blank to a store, with the colours and sizes that store sells."""

    def blank_for(self, request):
        return get_object_or_404(Blank, pk=request.GET.get("style") or request.POST.get("style"))

    def post(self, request, pk):
        store = self.get_store()
        blank = self.blank_for(request)
        form = PickBlankForm(request.POST, request.FILES, blank=blank)
        if not form.is_valid():
            return self.render_tab(store, "products", add_form=form)

        with transaction.atomic():
            offering = StoreProduct.objects.create(
                store=store, blank=blank,
                display_name=form.cleaned_data["display_name"],
                description=form.cleaned_data["description"],
                image=form.cleaned_data["image"],
                price=form.cleaned_data["price"],
                sort_order=(store.offerings.aggregate(m=Max("sort_order"))["m"] or 0) + 1,
            )
            offering.offered_variants.set(form.chosen_variants())

        messages.success(
            request,
            f"Added {offering.name} to {store.name} — "
            f"{len(form.chosen_variants())} colour and size options.",
        )
        return redirect(self.tab_url(store, "products"))


class StoreCreateView(StaffRequiredMixin, CreateView):
    model, form_class = Store, StoreForm
    template_name = "console/form.html"
    success_url = reverse_lazy("console:stores")
    extra_context = {"title": "New store"}

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        record_manual_change(self.object, "", self.request.user)
        return response


class StoreUpdateView(StaffRequiredMixin, UpdateView):
    model, form_class = Store, StoreForm
    template_name = "console/form.html"
    extra_context = {"title": "Edit store"}

    @transaction.atomic
    def form_valid(self, form):
        # The form has already copied the new status onto the instance, so ask the database.
        from_status = Store.objects.select_for_update().values_list("status", flat=True).get(pk=self.object.pk)
        response = super().form_valid(form)
        record_manual_change(self.object, from_status, self.request.user)
        return response

    def get_success_url(self):
        return reverse("console:store_detail", args=[self.object.pk])


# Staff hand-key from this, so the established columns never move: new ones go on the end.
CSV_COLUMNS = [
    "order_number", "paid_at", "buyer_name", "buyer_email", "buyer_phone", "recipient_name",
    "product_name", "variant_label", "sku", "quantity", "unit_price", "line_total", "order_total", "notes",
    "fulfillment_mode", "recipient_label", "delivery_fee", "delivery_location", "delivery_address",
]


class StoreOrdersCSVView(StoreDetailMixin, View):
    """One row per item, in the column order the ops system is keyed from."""

    def get(self, request, pk):
        store = self.get_store()
        orders = filter_orders(store.orders.all(), request.GET).prefetch_related("items")

        # Where the whole batch goes, repeated on every row: ops keys one line at a time.
        location = store.delivery_location_name if store.is_group else ""
        address = " / ".join(l.strip() for l in store.delivery_address.splitlines() if l.strip()) if store.is_group else ""

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
                    store.fulfillment_mode, item.recipient_label, f"{order.delivery_fee:.2f}", location, address,
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
            can_resend_confirmation=self.object.status == Order.Status.PAID,
            confirmations=emails_about(self.object).filter(kind=ORDER_CONFIRMATION),
        )
        return ctx


class OrderResendConfirmationView(StaffRequiredMixin, View):
    """Queue the buyer's confirmation email again. Paid orders only."""

    def post(self, request, order_number):
        order = get_object_or_404(Order.objects.select_related("store__client"), order_number=order_number)
        if order.status != Order.Status.PAID:
            messages.error(request, "Only paid orders can have their confirmation re-sent.")
        else:
            queue_order_confirmation(order)
            messages.success(request, f"Confirmation email queued for {order.buyer_email}. It goes out within a minute or two.")
        return redirect("console:order_detail", order_number=order.order_number)


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


class TeamRequiredMixin(StaffRequiredMixin):
    """Only owners and managers get near the team pages."""

    def test_func(self):
        return super().test_func() and can_manage_team(self.request.user)


class OutboxListView(TeamRequiredMixin, ListView):
    """Every email the outbox has queued, for troubleshooting delivery."""

    template_name = "console/outbox_list.html"
    context_object_name = "emails"
    paginate_by = 50

    def status(self):
        status = self.request.GET.get("status") or ""
        return status if status in OutboxEmail.Status.values else ""

    def get_queryset(self):
        emails = OutboxEmail.objects.order_by("-created_at", "-pk")
        return emails.filter(status=self.status()) if self.status() else emails

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            status=self.status(),
            statuses=OutboxEmail.Status.choices,
            status_query=urlencode({"status": self.status()}) if self.status() else "",
        )
        return ctx


class OutboxRetryView(TeamRequiredMixin, View):
    """Try a failed email again from scratch."""

    def post(self, request, pk):
        email = get_object_or_404(OutboxEmail, pk=pk)
        if email.status != OutboxEmail.Status.FAILED:
            messages.error(request, "Only failed emails can be retried.")
        else:
            retry(email)
            messages.success(request, f"Email to {email.to_email} queued again. It goes out within a minute or two.")
        next_url = request.POST.get("next") or ""
        if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
            next_url = reverse("console:emails")
        return redirect(next_url)


class TeamListView(TeamRequiredMixin, ListView):
    template_name = "console/team_list.html"
    context_object_name = "profiles"

    def get_queryset(self):
        profiles = StaffProfile.objects.select_related("user")
        search = (self.request.GET.get("q") or "").strip()
        if search:
            profiles = profiles.filter(
                Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__email__icontains=search)
            )
        return profiles

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search"] = (self.request.GET.get("q") or "").strip()
        return ctx


class TeamInviteView(TeamRequiredMixin, FormView):
    template_name = "console/team_form.html"
    form_class = TeamInviteForm
    extra_context = {"title": "Invite someone", "submit_label": "Send invitation"}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "actor": self.request.user}

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save()
        send_invitation(self.request, user, self.request.user)
        messages.success(self.request, f"Invitation sent to {user.email}.")
        return redirect("console:team")


class TeamMemberMixin(TeamRequiredMixin):
    """Finds the colleague being acted on, and bows out politely when they're off limits."""

    def get_member(self):
        return get_object_or_404(get_user_model().objects.select_related("staff_profile"), pk=self.kwargs["pk"], is_staff=True)

    def refuse(self, reason):
        messages.error(self.request, reason)
        return redirect("console:team")


class TeamMemberView(TeamMemberMixin, UpdateView):
    template_name = "console/team_form.html"
    form_class = TeamMemberForm

    def get(self, request, *args, **kwargs):
        allowed, reason = can_edit(request.user, self.get_member())
        return super().get(request, *args, **kwargs) if allowed else self.refuse(reason)

    def post(self, request, *args, **kwargs):
        allowed, reason = can_edit(request.user, self.get_member())
        return super().post(request, *args, **kwargs) if allowed else self.refuse(reason)

    def get_object(self, queryset=None):
        return self.get_member()

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "actor": self.request.user}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile = profile_for(self.object)
        ctx.update(
            title=profile.display_name,
            submit_label="Save changes",
            member=self.object,
            profile=profile,
            can_deactivate=can_set_active(self.request.user, self.object, False)[0],
            deactivate_reason=can_set_active(self.request.user, self.object, False)[1],
        )
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Saved {profile_for(self.object).display_name}.")
        return response

    def get_success_url(self):
        return reverse("console:team")


class TeamMemberStatusView(TeamMemberMixin, View):
    """Deactivate or reactivate a colleague. Accounts are never deleted."""

    def post(self, request, pk):
        member = self.get_member()
        active = request.POST.get("active") == "1"
        allowed, reason = can_set_active(request.user, member, active)
        if not allowed:
            return self.refuse(reason)

        member.is_active = active
        member.save(update_fields=["is_active"])
        name = profile_for(member).display_name
        messages.success(
            request,
            f"{name} can sign in again." if active else f"{name} can no longer sign in. Their past work is untouched.",
        )
        return redirect("console:team")


class TeamResendInviteView(TeamMemberMixin, View):
    def post(self, request, pk):
        member = self.get_member()
        allowed, reason = can_edit(request.user, member)
        if not allowed:
            return self.refuse(reason)
        if not profile_for(member).is_invited:
            messages.error(request, f"{profile_for(member).display_name} has already set up their account.")
            return redirect("console:team")

        send_invitation(request, member, request.user)
        messages.success(request, f"New invitation sent to {member.email}.")
        return redirect("console:team")


class InvitationAcceptView(FormView):
    """Public: the invitee sets their password, which switches the account on."""

    template_name = "console/invitation_accept.html"
    form_class = ConsoleSetPasswordForm

    def dispatch(self, request, *args, **kwargs):
        try:
            self.invited_user = user_from_token(kwargs["token"])
        except InvitationInvalid as problem:
            return render(request, "console/invitation_invalid.html", {"reason": problem.message}, status=400)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.invited_user}

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"invited_user": self.invited_user}

    def form_valid(self, form):
        user = form.save()
        user.is_active = True
        user.save(update_fields=["is_active"])
        sign_in(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(self.request, f"Welcome to Inked Graphics, {profile_for(user).display_name}.")
        return redirect("console:dashboard")
