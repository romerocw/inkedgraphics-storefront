from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone as dj_timezone

from messaging.models import OutboxEmail
from stores.arrival import estimated_arrival
from stores.factories import (
    make_client, make_group_store, make_offering, make_order, make_product, make_staff, make_store,
)
from stores.models import Store

from .links import order_link_key, order_url
from .models import Order, allowed_transitions, change_status, mark_paid


class StatusTransitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = make_store()
        cls.offering = make_offering(cls.store, price="40.00")
        cls.user = make_staff()

    def order(self, status):
        return make_order(self.store, status=status, items=[(self.offering, 2)])

    def test_paid_can_be_sent_to_production(self):
        order = self.order(Order.Status.PAID)
        change = change_status(order, Order.Status.SENT_TO_OPS, self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.SENT_TO_OPS)
        self.assertEqual((change.from_status, change.to_status), (Order.Status.PAID, Order.Status.SENT_TO_OPS))
        self.assertEqual(change.changed_by, self.user)

    def test_sent_to_production_can_be_fulfilled(self):
        order = self.order(Order.Status.SENT_TO_OPS)
        change_status(order, Order.Status.FULFILLED, self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FULFILLED)

    def test_paid_and_sent_can_be_cancelled_with_a_note(self):
        for status in (Order.Status.PAID, Order.Status.SENT_TO_OPS):
            with self.subTest(status=status):
                order = self.order(status)
                change = change_status(order, Order.Status.CANCELLED, self.user, note="Buyer asked to cancel")
                order.refresh_from_db()
                self.assertEqual(order.status, Order.Status.CANCELLED)
                self.assertEqual(change.note, "Buyer asked to cancel")

    def test_cancelling_without_a_note_is_refused(self):
        order = self.order(Order.Status.PAID)
        for note in ("", "   "):
            with self.subTest(note=repr(note)):
                with self.assertRaises(ValidationError):
                    change_status(order, Order.Status.CANCELLED, self.user, note=note)
                order.refresh_from_db()
                self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.status_changes.count(), 0)

    def test_forbidden_transitions_are_refused(self):
        forbidden = [
            (Order.Status.PAID, Order.Status.FULFILLED),
            (Order.Status.PAID, Order.Status.PENDING),
            (Order.Status.PAID, Order.Status.REFUNDED),
            (Order.Status.PENDING, Order.Status.PAID),
            (Order.Status.PENDING, Order.Status.SENT_TO_OPS),
            (Order.Status.SENT_TO_OPS, Order.Status.PAID),
            (Order.Status.FULFILLED, Order.Status.CANCELLED),
            (Order.Status.FULFILLED, Order.Status.SENT_TO_OPS),
            (Order.Status.CANCELLED, Order.Status.PAID),
            (Order.Status.REFUNDED, Order.Status.PAID),
        ]
        for current, target in forbidden:
            with self.subTest(current=current, target=target):
                order = self.order(current)
                with self.assertRaises(ValidationError):
                    change_status(order, target, self.user, note="trying anyway")
                order.refresh_from_db()
                self.assertEqual(order.status, current)
                self.assertEqual(order.status_changes.count(), 0)

    def test_final_statuses_offer_no_transitions(self):
        for status in (Order.Status.PENDING, Order.Status.FULFILLED, Order.Status.CANCELLED, Order.Status.REFUNDED):
            with self.subTest(status=status):
                self.assertEqual(allowed_transitions(self.order(status)), [])

    def test_amounts_are_never_touched(self):
        order = self.order(Order.Status.PAID)
        subtotal, total, paid_at = order.subtotal, order.total, order.paid_at
        change_status(order, Order.Status.SENT_TO_OPS, self.user)
        order.refresh_from_db()
        self.assertEqual((order.subtotal, order.total, order.paid_at), (subtotal, total, paid_at))

    def test_anonymous_user_is_logged_as_nobody(self):
        order = self.order(Order.Status.PAID)
        change = change_status(order, Order.Status.SENT_TO_OPS, AnonymousUser())
        self.assertIsNone(change.changed_by)


@override_settings(SITE_URL="https://store.example.com")
class OrderConfirmationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        client = make_client(name="Langley High", primary_color="#1e3a8a")
        cls.store = make_store(client=client, name="Spring Spirit Wear", closes_at=datetime(2026, 10, 3, 18, tzinfo=dt_timezone.utc))
        cls.hoodie = make_offering(cls.store, make_product(name="Tee & Hoodie", variants=(("Navy", "L"),)), price="40.00")
        cls.cap = make_offering(cls.store, make_product(name="Cap", variants=(("Red", "OS"),)), price="15.00")

    def pending_order(self, **kwargs):
        kwargs.setdefault("buyer_name", "Pat O'Brien")
        kwargs.setdefault("buyer_email", "pat@example.com")
        return make_order(self.store, status=Order.Status.PENDING, items=[(self.hoodie, 2), (self.cap, 1)], **kwargs)

    def confirmations(self):
        return OutboxEmail.objects.filter(kind="order_confirmation")

    def test_marking_paid_queues_one_confirmation_to_the_buyer(self):
        order = self.pending_order()
        mark_paid(order, "pi_123")

        order.refresh_from_db()
        self.assertEqual((order.status, order.stripe_payment_intent), (Order.Status.PAID, "pi_123"))
        self.assertIsNotNone(order.paid_at)
        (email,) = self.confirmations()
        self.assertEqual((email.to_name, email.to_email), ("Pat O'Brien", "pat@example.com"))
        self.assertEqual(email.subject, f"Order {order.order_number} confirmed — Spring Spirit Wear")
        self.assertEqual(email.related, order)
        self.assertEqual(email.status, OutboxEmail.Status.QUEUED)
        self.assertEqual(mail.outbox, [])  # queued, not sent inline

    def test_marking_paid_twice_queues_only_once(self):
        order = self.pending_order()
        mark_paid(order)
        mark_paid(order)
        self.assertEqual(self.confirmations().count(), 1)

    def test_a_second_caller_with_a_stale_copy_does_not_queue_again(self):
        # The success page and the webhook each load the order; both see it pending.
        order = self.pending_order()
        other_copy = Order.objects.get(pk=order.pk)
        mark_paid(order, "pi_123")
        mark_paid(other_copy, "pi_123")

        self.assertEqual(self.confirmations().count(), 1)
        self.assertEqual(other_copy.status, Order.Status.PAID)  # refreshed, not left pending

    def test_orders_that_are_not_pending_are_left_alone(self):
        order = make_order(self.store, status=Order.Status.CANCELLED)
        mark_paid(order)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertFalse(self.confirmations().exists())

    def test_email_lists_the_order(self):
        order = self.pending_order(recipient_name="Sam O'Brien")
        mark_paid(order)
        (email,) = self.confirmations()

        for body in (email.text_body, email.html_body):
            with self.subTest(body=body[:20]):
                self.assertIn(order.order_number, body)
                self.assertIn("Spring Spirit Wear", body)
                self.assertIn("Navy / L", body)
                self.assertIn("$80.00", body)
                self.assertIn("$15.00", body)
                self.assertIn("$95.00", body)
                self.assertIn("October 3, 2026 at 2:00 p.m. EDT", body)  # 18:00 UTC, Eastern store
                self.assertIn("produced after the store closes", body)
            self.assertIn(order_url(order), email.text_body)
        self.assertIn(order_url(order), email.html_body)
        self.assertIn("2 x Tee & Hoodie (Navy / L)", email.text_body)
        self.assertIn("Items are for: Sam O'Brien", email.text_body)  # plain text isn't HTML-escaped
        self.assertIn("Tee &amp; Hoodie", email.html_body)
        self.assertIn("Sam O&#x27;Brien", email.html_body)

    def test_email_uses_the_store_branding(self):
        mark_paid(self.pending_order())
        html = self.confirmations().get().html_body
        self.assertIn("background:#1e3a8a", html)
        self.assertIn("Langley High", html)

        Store.objects.filter(pk=self.store.pk).update(primary_color="#228b22")
        self.store.refresh_from_db()
        mark_paid(self.pending_order())
        self.assertIn("background:#228b22", self.confirmations().first().html_body)

    def test_email_copes_without_recipient_or_close_date(self):
        Store.objects.filter(pk=self.store.pk).update(closes_at=None)
        self.store.refresh_from_db()
        mark_paid(self.pending_order())
        (email,) = self.confirmations()
        self.assertNotIn("Items are for", email.text_body)
        self.assertIn("end of the ordering period", email.text_body)

    def test_email_link_opens_the_order_in_any_browser(self):
        order = self.pending_order()
        mark_paid(order)

        url = order_url(order)
        self.assertTrue(url.startswith(f"https://store.example.com/order/{order.order_number}/?k="))
        path = url.removeprefix("https://store.example.com")
        response = self.client.get(path)
        self.assertContains(response, order.order_number)
        # ...and the browser remembers it without the key.
        self.assertContains(self.client.get(f"/order/{order.order_number}/"), order.order_number)

    def test_bad_or_borrowed_keys_do_not_open_an_order(self):
        order, other = self.pending_order(), self.pending_order()
        for key in ("", "nonsense", order_link_key(other.order_number)):
            with self.subTest(key=key):
                response = self.client.get(f"/order/{order.order_number}/", {"k": key})
                self.assertRedirects(response, "/cart/", fetch_redirect_response=False)

    def test_thank_you_page_marks_paid_and_says_an_email_is_coming(self):
        order = self.pending_order(stripe_checkout_session="cs_123")
        session = self.client.session
        session["my_orders"] = [order.order_number]
        session.save()
        paid = SimpleNamespace(payment_status="paid", payment_intent="pi_123")
        with patch("orders.payments.stripe.checkout.Session.retrieve", return_value=paid):
            response = self.client.get(f"/order/{order.order_number}/success/", {"session_id": "cs_123"})
            self.client.get(f"/order/{order.order_number}/success/", {"session_id": "cs_123"})

        self.assertContains(response, "A confirmation email is on its way to <strong>pat@example.com</strong>")
        self.assertEqual(self.confirmations().count(), 1)

    def test_webhook_delivered_twice_queues_once(self):
        order = self.pending_order()
        event = {"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "payment_intent": "pi_123", "metadata": {"order_number": order.order_number},
        }}}
        with patch("orders.payments.stripe.Webhook.construct_event", return_value=event):
            for _ in range(2):
                self.assertEqual(self.client.post("/stripe/webhook/", b"{}", content_type="application/json").status_code, 200)
        self.assertEqual(self.confirmations().count(), 1)


class ArrivalPromiseTests(TestCase):
    """One promise, shown identically everywhere a buyer can see it, and frozen onto the order."""

    def setUp(self):
        self.store = make_store(
            name="Spring Spirit Wear", closes_at=dj_timezone.now() + timedelta(days=14)
        )
        self.offering = make_offering(self.store, price="40.00")
        self.expected = estimated_arrival(self.store).label

    def add_to_cart(self):
        variant = self.offering.product.variants.first()
        return self.client.post(
            reverse("cart_add", args=[self.store.slug]),
            {"store_product": self.offering.pk, "variant": variant.pk, "quantity": 1},
        )

    def test_store_page_cart_and_checkout_all_quote_the_same_dates(self):
        pages = [self.client.get(reverse("store_detail", args=[self.store.slug]))]
        self.add_to_cart()
        pages.append(self.client.get(reverse("cart")))
        pages.append(self.client.get(reverse("checkout")))
        for page in pages:
            self.assertContains(page, f"Arrives {self.expected}")

    def test_checkout_freezes_the_promise_onto_the_order(self):
        self.add_to_cart()
        response = self.client.post(
            reverse("checkout"), {"buyer_name": "Pat O'Brien", "buyer_email": "pat@example.com"}
        )
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.promised_arrival.label, self.expected)

    def test_the_confirmation_email_quotes_what_the_buyer_was_promised(self):
        self.add_to_cart()
        self.client.post(reverse("checkout"), {"buyer_name": "Pat O'Brien", "buyer_email": "pat@example.com"})
        order = Order.objects.get()
        mark_paid(order)

        (email,) = OutboxEmail.objects.filter(kind="order_confirmation")
        self.assertIn(f"Arrives {self.expected}", email.text_body)
        self.assertIn(self.expected, email.html_body)

    def test_the_email_keeps_the_promise_even_after_the_store_moves_its_close_date(self):
        # The outbox sends from cron, long after checkout. The buyer keeps what they were told.
        self.add_to_cart()
        self.client.post(reverse("checkout"), {"buyer_name": "Pat O'Brien", "buyer_email": "pat@example.com"})
        order = Order.objects.get()
        self.store.closes_at += timedelta(days=30)
        self.store.save(update_fields=["closes_at"])
        self.assertNotEqual(self.store.arrival.label, self.expected)

        mark_paid(order)
        (email,) = OutboxEmail.objects.filter(kind="order_confirmation")
        self.assertIn(f"Arrives {self.expected}", email.text_body)

    def test_a_store_with_no_close_date_promises_nothing_anywhere(self):
        store = make_store(closes_at=None)
        make_offering(store, price="40.00")
        self.assertNotContains(self.client.get(reverse("store_detail", args=[store.slug])), "Arrives")


class FulfillmentModeTests(TestCase):
    """Three modes, priced three ways: nothing, a per-buyer share, and the school's own bill."""

    def setUp(self):
        self.individual = make_store(name="Ship To Me")
        self.group_ship = make_group_store(Store.Fulfillment.GROUP_SHIP, group_ship_fee="6.50")
        self.group_delivery = make_group_store(Store.Fulfillment.GROUP_DELIVERY)

    def test_only_group_ship_charges_the_buyer(self):
        self.assertEqual(self.individual.buyer_delivery_fee, Decimal("0"))
        self.assertEqual(self.group_ship.buyer_delivery_fee, Decimal("6.50"))
        self.assertEqual(self.group_delivery.buyer_delivery_fee, Decimal("0"))

    def test_only_group_delivery_bills_the_organization(self):
        self.assertIsNone(self.individual.organization_delivery_fee)
        self.assertIsNone(self.group_ship.organization_delivery_fee)
        self.assertEqual(self.group_delivery.organization_delivery_fee, Decimal("30"))

    @override_settings(DEFAULT_GROUP_DELIVERY_FEE=45)
    def test_the_drop_off_fee_falls_back_to_the_site_default(self):
        self.assertEqual(self.group_delivery.organization_delivery_fee, Decimal("45"))
        self.group_delivery.group_delivery_fee = Decimal("20.00")
        self.assertEqual(self.group_delivery.organization_delivery_fee, Decimal("20.00"))

    def test_group_modes_are_grouped_and_individual_is_not(self):
        self.assertFalse(self.individual.is_group)
        self.assertTrue(self.group_ship.is_group)
        self.assertTrue(self.group_delivery.is_group)


class GroupCheckoutTests(TestCase):
    """One buyer, several children: each line carries its own name all the way through."""

    def setUp(self):
        self.store = make_group_store(Store.Fulfillment.GROUP_DELIVERY)
        self.hoodie = make_offering(self.store, make_product(name="Hoodie", variants=(("Navy", "M"),)), price="40.00")

    def add(self, qty=1):
        variant = self.hoodie.product.variants.first()
        self.client.post(
            reverse("cart_add", args=[self.store.slug]),
            {"store_product": self.hoodie.pk, "variant": variant.pk, "quantity": qty},
        )

    def cart_keys(self):
        return list(self.client.session["cart"]["lines"])

    def label_all(self, *labels, **extra):
        keys = self.cart_keys()
        data = {f"label:{key}": label for key, label in zip(keys, labels)}
        data.update({f"qty:{key}": "1" for key in keys})
        data.update(extra)
        return self.client.post(reverse("cart_update"), data)

    def checkout(self):
        return self.client.post(
            reverse("checkout"), {"buyer_name": "Pat O'Brien", "buyer_email": "pat@example.com"}
        )

    def test_the_same_item_added_twice_stays_two_labellable_lines(self):
        self.add()
        self.add()
        self.assertEqual(len(self.cart_keys()), 2)

    def test_an_individual_ship_store_still_merges_identical_lines(self):
        store = make_store()
        offering = make_offering(store, price="40.00")
        variant = offering.product.variants.first()
        for _ in range(2):
            self.client.post(
                reverse("cart_add", args=[store.slug]),
                {"store_product": offering.pk, "variant": variant.pk, "quantity": 1},
            )
        (line,) = self.client.session["cart"]["lines"].values()
        self.assertEqual(line["qty"], 2)

    def test_labels_reach_the_order_lines(self):
        self.add()
        self.add()
        self.label_all("Ava – 5th grade", "Ben – 2nd grade")
        self.checkout()

        order = Order.objects.get()
        self.assertEqual(
            sorted(item.recipient_label for item in order.items.all()),
            ["Ava – 5th grade", "Ben – 2nd grade"],
        )

    def test_checkout_is_refused_until_every_line_is_named(self):
        self.add()
        self.add()
        self.label_all("Ava – 5th grade", "")

        response = self.client.post(
            reverse("checkout"), {"buyer_name": "Pat", "buyer_email": "pat@example.com"}, follow=True
        )
        self.assertContains(response, "who each item is for")
        self.assertFalse(Order.objects.exists())

    def test_the_checkout_button_saves_the_labels_on_its_way(self):
        self.add()
        response = self.label_all("Ava – 5th grade", checkout="1")
        self.assertRedirects(response, reverse("checkout"))
        self.assertContains(self.client.get(reverse("checkout")), "Delivered to")

    def test_a_group_store_asks_for_no_order_wide_recipient(self):
        self.add()
        self.label_all("Ava – 5th grade")
        self.assertNotContains(self.client.get(reverse("checkout")), "Player / student name")

    def test_the_delivery_address_is_shown_instead_of_a_shipping_form(self):
        self.add()
        self.label_all("Ava – 5th grade")
        page = self.client.get(reverse("checkout"))
        self.assertContains(page, "Langley High front office")
        self.assertContains(page, "Nothing is shipped to you")

    def test_group_delivery_adds_nothing_to_what_the_buyer_pays(self):
        self.add()
        self.label_all("Ava – 5th grade")
        self.checkout()

        order = Order.objects.get()
        self.assertEqual(order.delivery_fee, Decimal("0"))
        self.assertEqual(order.total, order.subtotal)

    def test_the_confirmation_email_says_where_to_collect_it(self):
        self.add()
        self.label_all("Ava – 5th grade")
        self.checkout()
        mark_paid(Order.objects.get())

        (email,) = OutboxEmail.objects.filter(kind="order_confirmation")
        self.assertIn("Langley High front office", email.text_body)
        self.assertIn("Ava – 5th grade", email.text_body)
        self.assertIn("Ava – 5th grade", email.html_body)


class GroupShipFeeTests(TestCase):
    """Group ship charges each buyer a staff-set share of the consignment."""

    def setUp(self):
        self.store = make_group_store(Store.Fulfillment.GROUP_SHIP, group_ship_fee="6.50")
        self.offering = make_offering(self.store, price="40.00")
        variant = self.offering.product.variants.first()
        self.client.post(
            reverse("cart_add", args=[self.store.slug]),
            {"store_product": self.offering.pk, "variant": variant.pk, "quantity": 2},
        )
        (key,) = self.client.session["cart"]["lines"]
        self.client.post(reverse("cart_update"), {f"label:{key}": "Ava", f"qty:{key}": "2"})

    def order(self):
        self.client.post(reverse("checkout"), {"buyer_name": "Pat", "buyer_email": "pat@example.com"})
        return Order.objects.get()

    def test_the_fee_is_snapshotted_and_added_to_the_total(self):
        order = self.order()
        self.assertEqual(order.subtotal, Decimal("80.00"))
        self.assertEqual(order.delivery_fee, Decimal("6.50"))
        self.assertEqual(order.total, Decimal("86.50"))

    def test_the_fee_survives_the_store_changing_its_mind(self):
        order = self.order()
        self.store.group_ship_fee = Decimal("99.00")
        self.store.save(update_fields=["group_ship_fee"])
        order.recalculate()
        self.assertEqual(order.total, Decimal("86.50"))

    def test_the_cart_and_checkout_show_the_fee(self):
        for url in (reverse("cart"), reverse("checkout")):
            self.assertContains(self.client.get(url), "6.50")

    def test_the_fee_reaches_stripe_as_its_own_line(self):
        order = self.order()
        with patch("orders.payments.stripe.checkout.Session.create") as create:
            create.return_value = SimpleNamespace(id="cs_1", url="https://stripe.test/pay")
            self.client.get(reverse("order_pay", args=[order.order_number]))
        names = [li["price_data"]["product_data"]["name"] for li in create.call_args.kwargs["line_items"]]
        amounts = [li["price_data"]["unit_amount"] for li in create.call_args.kwargs["line_items"]]
        self.assertIn("Delivery", names)
        self.assertEqual(amounts[names.index("Delivery")], 650)

    def test_an_individual_ship_order_sends_no_delivery_line(self):
        store = make_store()
        offering = make_offering(store, price="40.00")
        order = make_order(store, status=Order.Status.PENDING, items=[(offering, 1)])
        session = self.client.session
        session["my_orders"] = [order.order_number]
        session.save()
        with patch("orders.payments.stripe.checkout.Session.create") as create:
            create.return_value = SimpleNamespace(id="cs_1", url="https://stripe.test/pay")
            self.client.get(reverse("order_pay", args=[order.order_number]))
        names = [li["price_data"]["product_data"]["name"] for li in create.call_args.kwargs["line_items"]]
        self.assertNotIn("Delivery", names)
