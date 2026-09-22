import csv
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductVariant
from orders.models import Order, OrderItem
from stores.factories import (
    make_client, make_group_store, make_offering, make_order, make_owner, make_product, make_staff, make_store,
)
from stores.models import Store

from .views import packout_groups


class PasswordChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("console:password_change")
        cls.user = get_user_model().objects.create_user("staffer", password="old-pass-9271", is_staff=True)

    def test_anonymous_redirected_to_login(self):
        self.assertRedirects(self.client.get(self.url), f"{reverse('console:login')}?next={self.url}")

    def test_non_staff_forbidden(self):
        get_user_model().objects.create_user("buyer", password="old-pass-9271")
        self.client.login(username="buyer", password="old-pass-9271")
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_page_renders_for_staff(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, "Change password")

    def test_password_changed_and_session_kept(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"old_password": "old-pass-9271", "new_password1": "new-pass-4823", "new_password2": "new-pass-4823"},
        )
        self.assertRedirects(response, reverse("console:my_account"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-pass-4823"))
        self.assertEqual(self.client.get(reverse("console:dashboard")).status_code, 200)

    def test_wrong_old_password_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"old_password": "wrong-pass", "new_password1": "new-pass-4823", "new_password2": "new-pass-4823"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("old_password", response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-pass-9271"))


class StoreProductsTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.hoodie = make_product(name="Hoodie", sku_prefix="HOOD")
        self.tee = make_product(name="Tee", sku_prefix="TEE", default_price="20.00")
        self.retired = make_product(name="Retired Jacket", sku_prefix="JKT", is_active=False)
        self.client.force_login(make_staff())

    def products_url(self, tab="products"):
        return f"{reverse('console:store_detail', args=[self.store.pk])}?tab={tab}"

    def test_add_panel_offers_only_active_products_not_yet_in_the_store(self):
        make_offering(self.store, self.hoodie)
        response = self.client.get(self.products_url())
        self.assertEqual(list(response.context["add_form"].products), [self.tee])

    def test_add_selected_products_with_given_prices(self):
        response = self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]),
            {"add_selected": "1", f"add_{self.tee.pk}": "on", f"price_{self.tee.pk}": "25.50"},
            follow=True,
        )
        offering = self.store.offerings.get()
        self.assertEqual((offering.product, offering.price), (self.tee, Decimal("25.50")))
        self.assertTrue(offering.is_active)
        self.assertContains(response, "Added 1 product")

    def test_price_defaults_to_the_products_usual_price(self):
        self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]),
            {"add_selected": "1", f"add_{self.tee.pk}": "on", f"price_{self.tee.pk}": ""},
        )
        self.assertEqual(self.store.offerings.get().price, self.tee.default_price)

    def test_add_all_skips_products_already_in_the_store_and_inactive_ones(self):
        make_offering(self.store, self.hoodie, price="30.00")
        self.client.post(reverse("console:store_products_add", args=[self.store.pk]), {"add_all": "1"})
        self.assertEqual(
            sorted(self.store.offerings.values_list("product__name", flat=True)), ["Hoodie", "Tee"]
        )
        self.assertEqual(self.store.offerings.get(product=self.hoodie).price, Decimal("30.00"))

    def test_adding_nothing_tells_staff_what_to_do(self):
        response = self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]), {"add_selected": "1"}, follow=True
        )
        self.assertEqual(self.store.offerings.count(), 0)
        self.assertContains(response, "Tick the products you want to add")

    def test_a_bad_price_adds_nothing(self):
        response = self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]),
            {"add_selected": "1", f"add_{self.tee.pk}": "on", f"price_{self.tee.pk}": "-5"},
        )
        self.assertEqual(self.store.offerings.count(), 0)
        self.assertContains(response, "Nothing was added")

    def test_inline_edits_save_price_active_and_order(self):
        offering = make_offering(self.store, self.hoodie, price="40.00")
        response = self.client.post(
            reverse("console:store_products", args=[self.store.pk]),
            {
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
                "form-0-id": str(offering.pk), "form-0-display_name": "Langley Hoodie",
                "form-0-price": "42.00", "form-0-sort_order": "3",
            },
            follow=True,
        )
        offering.refresh_from_db()
        self.assertEqual((offering.display_name, offering.price, offering.sort_order), ("Langley Hoodie", Decimal("42.00"), 3))
        self.assertFalse(offering.is_active)  # unchecked box means "not for sale"
        self.assertContains(response, "Saved 1 product")

    def test_a_bad_inline_edit_saves_nothing(self):
        offering = make_offering(self.store, self.hoodie, price="40.00")
        response = self.client.post(
            reverse("console:store_products", args=[self.store.pk]),
            {
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
                "form-0-id": str(offering.pk), "form-0-price": "not a price", "form-0-sort_order": "0",
            },
        )
        offering.refresh_from_db()
        self.assertEqual(offering.price, Decimal("40.00"))
        self.assertContains(response, "weren&#x27;t saved")

    def test_summary_shows_paid_orders_and_revenue(self):
        offering = make_offering(self.store, self.hoodie, price="40.00")
        make_order(self.store, items=[(offering, 2)])
        make_order(self.store, status=Order.Status.PENDING, items=[(offering, 1)])
        response = self.client.get(reverse("console:store_detail", args=[self.store.pk]))
        self.assertEqual(response.context["paid_orders"], 1)
        self.assertEqual(response.context["revenue"], Decimal("80.00"))


class OrderListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = make_store(name="Lacrosse Store")
        cls.other_store = make_store(make_client("Vienna Middle", slug="vienna"), name="Band Store")
        cls.offering = make_offering(cls.store, price="40.00")
        cls.paid = make_order(
            cls.store, items=[(cls.offering, 1)],
            buyer_name="Dana Buyer", buyer_email="dana@example.com", recipient_name="Sam Player",
        )
        cls.pending = make_order(cls.store, status=Order.Status.PENDING, buyer_name="Chris Cart")
        cls.cancelled = make_order(cls.store, status=Order.Status.CANCELLED, buyer_name="Robin Gone")
        cls.elsewhere = make_offering(cls.other_store)
        cls.other_order = make_order(cls.other_store, buyer_name="Alex Band")

    def setUp(self):
        self.client.force_login(make_staff())

    def numbers(self, response):
        return {o.order_number for o in response.context["orders"]}

    def in_order(self, response):
        return [o.order_number for o in response.context["orders"]]

    def test_defaults_to_paid_statuses_across_stores(self):
        response = self.client.get(reverse("console:orders"))
        self.assertEqual(self.numbers(response), {self.paid.order_number, self.other_order.order_number})

    def test_every_status_filter_shows_everything(self):
        response = self.client.get(reverse("console:orders"), {"status": "all"})
        self.assertEqual(len(response.context["orders"]), 4)

    def test_single_status_filter(self):
        response = self.client.get(reverse("console:orders"), {"status": Order.Status.CANCELLED})
        self.assertEqual(self.numbers(response), {self.cancelled.order_number})

    def test_store_filter(self):
        response = self.client.get(reverse("console:orders"), {"store": self.other_store.pk})
        self.assertEqual(self.numbers(response), {self.other_order.order_number})

    def test_search_matches_number_buyer_and_recipient(self):
        for term in (self.paid.order_number, "dana", "dana@example.com", "sam play"):
            with self.subTest(term=term):
                response = self.client.get(reverse("console:orders"), {"q": term})
                self.assertEqual(self.numbers(response), {self.paid.order_number})

    def test_sorting_by_date_can_be_flipped(self):
        newest = self.in_order(self.client.get(reverse("console:orders"), {"status": "all"}))
        oldest = self.in_order(self.client.get(reverse("console:orders"), {"status": "all", "sort": "oldest"}))
        self.assertEqual(newest, list(reversed(oldest)))

    def test_pagination_is_fifty_per_page(self):
        for _ in range(51):
            make_order(self.store)
        response = self.client.get(reverse("console:orders"))
        self.assertEqual(len(response.context["orders"]), 50)
        self.assertTrue(response.context["page_obj"].has_next())

    def test_store_tab_shows_only_that_stores_orders(self):
        response = self.client.get(reverse("console:store_detail", args=[self.store.pk]), {"tab": "orders"})
        self.assertEqual(self.numbers(response), {self.paid.order_number})
        self.assertFalse(response.context["show_store"])


class OrderDetailTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.offering = make_offering(self.store, price="40.00")
        self.order = make_order(self.store, items=[(self.offering, 2)])
        self.user = make_staff()
        self.client.force_login(self.user)

    def url(self):
        return reverse("console:order_detail", args=[self.order.order_number])

    def post_status(self, to_status, note=""):
        return self.client.post(
            reverse("console:order_status", args=[self.order.order_number]),
            {"to_status": to_status, "note": note},
            follow=True,
        )

    def test_detail_shows_items_totals_and_payment(self):
        self.order.stripe_payment_intent = "pi_test_123"
        self.order.save(update_fields=["stripe_payment_intent"])
        response = self.client.get(self.url())
        self.assertContains(response, self.offering.product.name)
        self.assertContains(response, "80.00")
        self.assertContains(response, "pi_test_123")

    def test_only_allowed_moves_are_offered(self):
        response = self.client.get(self.url())
        self.assertEqual([status for status, _ in response.context["moves"]], [Order.Status.SENT_TO_OPS])
        self.assertTrue(response.context["can_cancel"])

    def test_fulfilled_order_offers_nothing(self):
        self.order.status = Order.Status.FULFILLED
        self.order.save(update_fields=["status"])
        response = self.client.get(self.url())
        self.assertEqual(response.context["moves"], [])
        self.assertFalse(response.context["can_cancel"])
        self.assertContains(response, "Nothing left to do")

    def test_allowed_move_is_applied_and_logged(self):
        response = self.post_status(Order.Status.SENT_TO_OPS)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SENT_TO_OPS)
        self.assertEqual(self.order.status_changes.get().changed_by, self.user)
        self.assertContains(response, "sent to production")

    def test_forbidden_move_is_refused_with_an_explanation(self):
        response = self.post_status(Order.Status.FULFILLED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertContains(response, "can&#x27;t be marked fulfilled")

    def test_cancelling_needs_a_note(self):
        response = self.post_status(Order.Status.CANCELLED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertContains(response, "say why you&#x27;re cancelling")

        self.post_status(Order.Status.CANCELLED, note="Buyer changed their mind")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.order.status_changes.get().note, "Buyer changed their mind")

    def test_totals_survive_a_status_change(self):
        self.post_status(Order.Status.SENT_TO_OPS)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total, Decimal("80.00"))


class BulkMarkSentTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.paid = make_order(self.store)
        self.also_paid = make_order(self.store)
        self.pending = make_order(self.store, status=Order.Status.PENDING)
        self.client.force_login(make_staff())

    def test_marks_only_paid_orders(self):
        response = self.client.post(
            reverse("console:orders_bulk"),
            {"orders": [self.paid.pk, self.also_paid.pk, self.pending.pk], "next": reverse("console:orders")},
            follow=True,
        )
        self.paid.refresh_from_db(), self.also_paid.refresh_from_db(), self.pending.refresh_from_db()
        self.assertEqual(self.paid.status, Order.Status.SENT_TO_OPS)
        self.assertEqual(self.also_paid.status, Order.Status.SENT_TO_OPS)
        self.assertEqual(self.pending.status, Order.Status.PENDING)
        self.assertContains(response, "Marked 2 orders sent to production")
        self.assertContains(response, "Left 1 order alone")

    def test_nothing_selected_says_so(self):
        response = self.client.post(reverse("console:orders_bulk"), {}, follow=True)
        self.assertContains(response, "Tick the orders you want to mark")

    def test_offsite_redirect_is_ignored(self):
        response = self.client.post(
            reverse("console:orders_bulk"), {"orders": [self.paid.pk], "next": "https://evil.example.com/"}
        )
        self.assertRedirects(response, reverse("console:orders"))


class OrdersCSVTests(TestCase):
    EXPECTED_COLUMNS = [
        "order_number", "paid_at", "buyer_name", "buyer_email", "buyer_phone", "recipient_name",
        "product_name", "variant_label", "sku", "quantity", "unit_price", "line_total", "order_total", "notes",
        "fulfillment_mode", "recipient_label", "delivery_fee", "delivery_location", "delivery_address",
    ]

    def setUp(self):
        self.store = make_store()
        self.hoodie = make_offering(
            self.store, make_product(name="Hoodie", sku_prefix="HOOD", variants=[("Black", "M"), ("Black", "L")]),
            price="40.00",
        )
        self.tee = make_offering(self.store, make_product(name="Tee", sku_prefix="TEE"), price="20.00")
        self.order = make_order(
            self.store, items=[(self.hoodie, 2), (self.tee, 1)],
            buyer_name="Dana Buyer", buyer_email="dana@example.com", buyer_phone="703-555-0101",
            recipient_name="Sam Player", notes="Please add a number 7",
        )
        self.client.force_login(make_staff())

    def fetch(self, **params):
        return self.client.get(reverse("console:store_orders_csv", args=[self.store.pk]), params)

    def rows(self, response):
        text = response.content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def test_starts_with_a_bom_so_excel_reads_accents(self):
        self.assertTrue(self.fetch().content.startswith(b"\xef\xbb\xbf"))

    def test_is_sent_as_a_download_named_after_the_store(self):
        response = self.fetch()
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(response["Content-Disposition"], f'attachment; filename="{self.store.slug}-orders.csv"')

    def test_column_order_is_what_the_ops_system_expects(self):
        self.assertEqual(self.rows(self.fetch())[0], self.EXPECTED_COLUMNS)

    def test_one_row_per_item_with_the_right_values(self):
        rows = self.rows(self.fetch())
        self.assertEqual(len(rows), 3)  # header + two items
        by_sku = {row[8]: row for row in rows[1:]}
        hoodie_row = by_sku["HOOD-BLACK-M"]
        self.assertEqual(
            hoodie_row,
            [
                self.order.order_number,
                timezone.localtime(self.order.paid_at).strftime("%Y-%m-%d %H:%M"),
                "Dana Buyer", "dana@example.com", "703-555-0101", "Sam Player",
                "Hoodie", "Black / M", "HOOD-BLACK-M", "2", "40.00", "80.00", "100.00",
                "Please add a number 7",
                "individual_ship", "", "0.00", "", "",
            ],
        )
        self.assertEqual(by_sku["TEE-BLACK-M"][9:13], ["1", "20.00", "20.00", "100.00"])

    def test_accented_names_survive_the_round_trip(self):
        self.order.buyer_name = "José Núñez"
        self.order.save(update_fields=["buyer_name"])
        self.assertIn("José Núñez", self.fetch().content.decode("utf-8-sig"))

    def test_defaults_to_paid_statuses_and_honors_the_status_filter(self):
        pending = make_order(self.store, status=Order.Status.PENDING, items=[(self.tee, 1)])
        self.assertEqual({row[0] for row in self.rows(self.fetch())[1:]}, {self.order.order_number})

        rows = self.rows(self.fetch(status=Order.Status.PENDING))
        self.assertEqual({row[0] for row in rows[1:]}, {pending.order_number})

        rows = self.rows(self.fetch(status="all"))
        self.assertEqual({row[0] for row in rows[1:]}, {self.order.order_number, pending.order_number})

    def test_only_this_stores_orders_are_exported(self):
        other = make_store()
        make_order(other, items=[(make_offering(other), 1)])
        self.assertEqual({row[0] for row in self.rows(self.fetch())[1:]}, {self.order.order_number})

    def test_a_store_with_no_orders_exports_just_the_header(self):
        self.order.delete()
        self.assertEqual(self.rows(self.fetch()), [self.EXPECTED_COLUMNS])


class ProductCatalogTests(TestCase):
    def setUp(self):
        self.client.force_login(make_staff())

    def variant_rows(self, rows, total=None, initial=0):
        data = {"variants-TOTAL_FORMS": str(total if total is not None else len(rows)), "variants-INITIAL_FORMS": str(initial)}
        for i, row in enumerate(rows):
            for field, value in row.items():
                data[f"variants-{i}-{field}"] = value
        return data

    def product_fields(self, **overrides):
        fields = {"name": "Unisex Hoodie", "sku_prefix": "HOOD-U", "default_price": "45.00", "is_active": "on"}
        fields.update(overrides)
        return fields

    def test_new_product_saves_with_its_variants(self):
        response = self.client.post(
            reverse("console:product_new"),
            {
                **self.product_fields(),
                **self.variant_rows([
                    {"color": "Black", "size": "M", "sku": "", "upcharge": "0", "is_active": "on", "sort_order": "0"},
                    {"color": "Black", "size": "2XL", "sku": "", "upcharge": "2.00", "is_active": "on", "sort_order": "1"},
                ]),
            },
            follow=True,
        )
        product = Product.objects.get(sku_prefix="HOOD-U")
        self.assertEqual(
            sorted(product.variants.values_list("sku", flat=True)), ["HOOD-U-BLACK-2XL", "HOOD-U-BLACK-M"]
        )
        self.assertEqual(product.variants.get(size="2XL").upcharge, Decimal("2.00"))
        self.assertContains(response, "Saved Unisex Hoodie")

    def test_sku_is_built_from_prefix_color_and_size_uppercased(self):
        self.client.post(
            reverse("console:product_new"),
            {
                **self.product_fields(sku_prefix="tee-w"),
                **self.variant_rows([{"color": "forest green", "size": "lg", "sku": "", "upcharge": "0", "sort_order": "0"}]),
            },
        )
        self.assertEqual(ProductVariant.objects.get().sku, "TEE-W-FOREST-GREEN-LG")

    def test_a_typed_sku_is_kept(self):
        self.client.post(
            reverse("console:product_new"),
            {
                **self.product_fields(),
                **self.variant_rows([{"color": "Black", "size": "M", "sku": "CUSTOM-1", "upcharge": "0", "sort_order": "0"}]),
            },
        )
        self.assertEqual(ProductVariant.objects.get().sku, "CUSTOM-1")

    def test_a_clashing_generated_sku_gets_a_suffix(self):
        # Another product already owns the SKU this one would generate.
        squatter = make_product(name="Old Hoodie", sku_prefix="OLD", variants=[])
        ProductVariant.objects.create(product=squatter, color="Black", size="M", sku="HOOD-U-BLACK-M")

        self.client.post(
            reverse("console:product_new"),
            {
                **self.product_fields(name="New Hoodie", sku_prefix="HOOD-U"),
                **self.variant_rows([{"color": "Black", "size": "M", "sku": "", "upcharge": "0", "sort_order": "0"}]),
            },
        )
        self.assertEqual(Product.objects.get(name="New Hoodie").variants.get().sku, "HOOD-U-BLACK-M-2")

    def test_editing_a_product_updates_and_removes_variants(self):
        product = make_product(name="Hoodie", sku_prefix="HOOD", variants=[("Black", "M"), ("Black", "L")])
        keep, drop = product.variants.order_by("pk")
        self.client.post(
            reverse("console:product_edit", args=[product.pk]),
            {
                **self.product_fields(name="Hoodie", sku_prefix="HOOD", default_price="50.00"),
                **self.variant_rows(
                    [
                        {"id": str(keep.pk), "color": "Black", "size": "M", "sku": keep.sku, "upcharge": "3.00", "is_active": "on", "sort_order": "5"},
                        {"id": str(drop.pk), "color": "Black", "size": "L", "sku": drop.sku, "upcharge": "0", "sort_order": "1", "DELETE": "on"},
                    ],
                    initial=2,
                ),
            },
        )
        product.refresh_from_db()
        keep.refresh_from_db()
        self.assertEqual(product.default_price, Decimal("50.00"))
        self.assertEqual((keep.upcharge, keep.sort_order), (Decimal("3.00"), 5))
        self.assertFalse(product.variants.filter(pk=drop.pk).exists())

    def test_a_duplicate_size_and_color_saves_nothing(self):
        response = self.client.post(
            reverse("console:product_new"),
            {
                **self.product_fields(),
                **self.variant_rows([
                    {"color": "Black", "size": "M", "sku": "", "upcharge": "0", "sort_order": "0"},
                    {"color": "Black", "size": "M", "sku": "", "upcharge": "0", "sort_order": "1"},
                ]),
            },
        )
        self.assertFalse(Product.objects.filter(sku_prefix="HOOD-U").exists())
        self.assertContains(response, "Nothing was saved")

    def test_list_search_and_active_filter(self):
        make_product(name="Cotton Tee", sku_prefix="TEE")
        make_product(name="Wool Hoodie", sku_prefix="HOOD")
        make_product(name="Retired Cap", sku_prefix="CAP", is_active=False)

        names = lambda response: {p.name for p in response.context["object_list"]}
        self.assertEqual(len(names(self.client.get(reverse("console:products")))), 3)
        self.assertEqual(names(self.client.get(reverse("console:products"), {"q": "hood"})), {"Wool Hoodie"})
        self.assertEqual(names(self.client.get(reverse("console:products"), {"q": "TEE"})), {"Cotton Tee"})
        self.assertEqual(names(self.client.get(reverse("console:products"), {"active": "0"})), {"Retired Cap"})
        self.assertEqual(
            names(self.client.get(reverse("console:products"), {"active": "1"})), {"Cotton Tee", "Wool Hoodie"}
        )

    def test_list_counts_variants(self):
        make_product(name="Hoodie", sku_prefix="HOOD", variants=[("Black", "M"), ("Black", "L")])
        response = self.client.get(reverse("console:products"))
        self.assertEqual(response.context["object_list"][0].variant_count, 2)


class ConsoleAccessTests(TestCase):
    """Nobody without is_staff gets into the console, on any page."""

    # Pages anyone may reach: signing in, signing out, and the forgotten-password steps
    # (someone locked out can't be asked to sign in first).
    PUBLIC = {
        "login", "logout",
        "password_reset", "password_reset_sent", "password_reset_confirm", "password_reset_done",
        "invitation_accept",  # the invitee has no account to sign in with yet
    }

    def setUp(self):
        self.store = make_store()
        self.order = make_order(self.store)
        self.product = make_product()
        self.shop_client = self.store.client
        self.colleague = make_staff()

    def staff_urls(self):
        return {
            "dashboard": [],
            "my_account": [],
            "password_change": [],
            "clients": [],
            "client_new": [],
            "client_edit": [self.shop_client.pk],
            "stores": [],
            "store_new": [],
            "store_detail": [self.store.pk],
            "store_edit": [self.store.pk],
            "store_products": [self.store.pk],
            "store_products_add": [self.store.pk],
            "store_orders_csv": [self.store.pk],
            "store_share_kit": [self.store.pk],
            "orders": [],
            "orders_bulk": [],
            "order_detail": [self.order.order_number],
            "order_status": [self.order.order_number],
            "order_resend_confirmation": [self.order.order_number],
            "products": [],
            "product_new": [],
            "product_edit": [self.product.pk],
            "team": [],
            "team_invite": [],
            "team_member": [self.colleague.pk],
            "team_member_status": [self.colleague.pk],
            "team_resend_invite": [self.colleague.pk],
            "emails": [],
            "email_retry": [1],
        }

    def test_every_console_page_is_in_this_test(self):
        from console import urls

        names = {pattern.name for pattern in urls.urlpatterns}
        self.assertEqual(names - self.PUBLIC, set(self.staff_urls()), "A new console page needs an access test here.")

    def test_a_signed_in_non_staff_user_is_refused(self):
        get_user_model().objects.create_user("buyer", password="buyer-pass-3391")
        self.client.login(username="buyer", password="buyer-pass-3391")
        for name, args in self.staff_urls().items():
            url = reverse(f"console:{name}", args=args)
            with self.subTest(page=name):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        for name, args in self.staff_urls().items():
            url = reverse(f"console:{name}", args=args)
            with self.subTest(page=name):
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('console:login')}?next={url}")

    def test_an_owner_can_reach_every_page_that_answers_a_get(self):
        # An owner, because the team pages are owner/manager only — the role rules
        # themselves are covered in tests_accounts.py.
        self.client.force_login(make_owner())
        post_only = {
            "store_products", "store_products_add", "orders_bulk", "order_status",
            "team_member_status", "team_resend_invite", "order_resend_confirmation", "email_retry",
            "store_share_kit",
        }
        for name, args in self.staff_urls().items():
            if name in post_only:
                continue
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"console:{name}", args=args)).status_code, 200)


class PackoutListTests(TestCase):
    """The floor's sorting sheet: every paid piece under the person it's for."""

    def setUp(self):
        self.store = make_group_store(Store.Fulfillment.GROUP_DELIVERY)
        self.hoodie = make_offering(self.store, make_product(name="Hoodie", sku_prefix="HOOD"), price="40.00")
        self.tee = make_offering(self.store, make_product(name="Tee", sku_prefix="TEE"), price="20.00")
        self.client.force_login(make_staff())

    def order_for(self, *labels, status=Order.Status.PAID, offering=None):
        order = make_order(self.store, status=status)
        for label in labels:
            OrderItem.objects.create(
                order=order, store_product=offering or self.hoodie,
                variant=(offering or self.hoodie).product.variants.first(),
                quantity=1, unit_price=Decimal("40.00"), recipient_label=label,
            )
        order.recalculate()
        return order

    def groups(self):
        return packout_groups(self.store)

    def test_lines_gather_under_their_recipient_across_orders(self):
        self.order_for("Ava", "Ben")
        self.order_for("Ava", offering=self.tee)
        self.assertEqual([g["recipient"] for g in self.groups()], ["Ava", "Ben"])
        self.assertEqual([g["pieces"] for g in self.groups()], [2, 1])

    def test_recipients_are_listed_alphabetically_ignoring_case(self):
        self.order_for("zoe", "Ava", "ben")
        self.assertEqual([g["recipient"] for g in self.groups()], ["Ava", "ben", "zoe"])

    def test_unlabelled_lines_collect_at_the_end_rather_than_vanishing(self):
        self.order_for("Ava", "")
        groups = self.groups()
        self.assertEqual([g["recipient"] for g in groups], ["Ava", "Not named"])

    def test_unpaid_orders_are_not_on_the_floor_sheet(self):
        self.order_for("Ava", status=Order.Status.PENDING)
        self.order_for("Ben")
        self.assertEqual([g["recipient"] for g in self.groups()], ["Ben"])

    def test_orders_already_in_production_stay_on_the_sheet(self):
        self.order_for("Ava", status=Order.Status.SENT_TO_OPS)
        self.assertEqual([g["recipient"] for g in self.groups()], ["Ava"])

    def test_the_tab_shows_only_for_group_stores(self):
        page = self.client.get(reverse("console:store_detail", args=[self.store.pk]))
        self.assertContains(page, "Pack-out")

        individual = make_store()
        page = self.client.get(reverse("console:store_detail", args=[individual.pk]))
        self.assertNotContains(page, "Pack-out")

    def test_asking_for_the_packout_tab_of_an_individual_store_falls_back_to_summary(self):
        individual = make_store()
        page = self.client.get(reverse("console:store_detail", args=[individual.pk]), {"tab": "packout"})
        self.assertContains(page, "Store details")

    def test_the_page_lists_each_recipient_with_their_items(self):
        self.order_for("Ava – 5th grade")
        page = self.client.get(reverse("console:store_detail", args=[self.store.pk]), {"tab": "packout"})
        self.assertContains(page, "Ava – 5th grade")
        self.assertContains(page, "HOOD-BLACK-M")
        self.assertContains(page, "window.print()")

    def test_an_empty_store_says_so_rather_than_showing_a_blank_sheet(self):
        page = self.client.get(reverse("console:store_detail", args=[self.store.pk]), {"tab": "packout"})
        self.assertContains(page, "Nothing paid for yet")


class GroupStoreCSVTests(TestCase):
    """The ops hand-off carries where the batch goes and who each line is for."""

    def setUp(self):
        self.store = make_group_store(Store.Fulfillment.GROUP_SHIP, group_ship_fee="6.50")
        self.offering = make_offering(self.store, make_product(name="Hoodie", sku_prefix="HOOD"), price="40.00")
        self.order = make_order(self.store, buyer_name="Dana Buyer")
        OrderItem.objects.create(
            order=self.order, store_product=self.offering, variant=self.offering.product.variants.first(),
            quantity=1, unit_price=Decimal("40.00"), recipient_label="Ava – 5th grade",
        )
        self.order.delivery_fee = Decimal("6.50")
        self.order.recalculate()
        self.client.force_login(make_staff())

    def rows(self):
        response = self.client.get(reverse("console:store_orders_csv", args=[self.store.pk]))
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_the_new_columns_are_appended_not_inserted(self):
        header = self.rows()[0]
        self.assertEqual(header[:14], OrdersCSVTests.EXPECTED_COLUMNS[:14])
        self.assertEqual(
            header[14:],
            ["fulfillment_mode", "recipient_label", "delivery_fee", "delivery_location", "delivery_address"],
        )

    def test_each_row_carries_the_mode_recipient_and_destination(self):
        row = self.rows()[1]
        self.assertEqual(
            row[14:],
            ["group_ship", "Ava – 5th grade", "6.50",
             "Langley High front office", "6520 Georgetown Pike / McLean, VA 22101"],
        )

    def test_the_multiline_address_stays_on_one_line(self):
        self.assertNotIn("\n", self.rows()[1][18])

    def test_an_individual_ship_store_leaves_the_destination_blank(self):
        store = make_store()
        offering = make_offering(store, price="40.00")
        make_order(store, items=[(offering, 1)])
        response = self.client.get(reverse("console:store_orders_csv", args=[store.pk]))
        row = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))[1]
        self.assertEqual(row[14:], ["individual_ship", "", "0.00", "", ""])
