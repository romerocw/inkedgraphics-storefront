import csv
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from orders.models import Order
from stores.factories import make_client, make_offering, make_order, make_product, make_staff, make_store


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
        self.assertRedirects(response, reverse("console:dashboard"))
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
