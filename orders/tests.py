from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import TestCase

from stores.factories import make_offering, make_order, make_staff, make_store

from .models import Order, allowed_transitions, change_status


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
