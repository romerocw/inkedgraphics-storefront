from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from messaging.models import OutboxEmail
from orders.models import Order
from stores.factories import make_manager, make_offering, make_order, make_owner, make_staff, make_store


def outbox_row(**kwargs):
    fields = {"kind": "order_confirmation", "to_email": "pat@example.com", "subject": "Order confirmed", "text_body": "Hi"}
    return OutboxEmail.objects.create(**{**fields, **kwargs})


class OutboxPageTests(TestCase):
    url = reverse("console:emails")

    def test_owners_and_managers_can_see_it(self):
        outbox_row(
            to_email="dana@example.com", status=OutboxEmail.Status.FAILED, attempts=2,
            last_error="SMTPException: nope", next_attempt_at=timezone.now(),
        )
        outbox_row(to_email="gone@example.com", status=OutboxEmail.Status.FAILED, attempts=5, last_error="nope")
        for role, user in (("owner", make_owner()), ("manager", make_manager())):
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(self.url)
                self.assertContains(response, "dana@example.com")
                self.assertContains(response, "SMTPException: nope")
                self.assertContains(response, "Failed — will retry")
                self.assertContains(response, "Failed — gave up")
                self.assertContains(response, 'href="/console/emails/"')  # nav link

    def test_staff_are_refused_and_do_not_see_the_link(self):
        self.client.force_login(make_staff())
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotContains(self.client.get(reverse("console:dashboard")), 'href="/console/emails/"')

    def test_signed_out_people_are_sent_to_sign_in(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('console:login')}?next={self.url}", fetch_redirect_response=False)

    def test_filters_by_status(self):
        outbox_row(subject="Went fine", status=OutboxEmail.Status.SENT)
        outbox_row(subject="Went wrong", status=OutboxEmail.Status.FAILED)
        self.client.force_login(make_owner())

        response = self.client.get(self.url, {"status": "failed"})
        self.assertContains(response, "Went wrong")
        self.assertNotContains(response, "Went fine")
        response = self.client.get(self.url, {"status": "bogus"})
        self.assertContains(response, "Went wrong")
        self.assertContains(response, "Went fine")

    def test_retry_requeues_a_failed_email(self):
        email = outbox_row(status=OutboxEmail.Status.FAILED, attempts=5, last_error="nope")
        self.client.force_login(make_manager())

        response = self.client.post(reverse("console:email_retry", args=[email.pk]), {"next": "/console/emails/?status=failed"})
        self.assertRedirects(response, "/console/emails/?status=failed", fetch_redirect_response=False)
        email.refresh_from_db()
        self.assertEqual((email.status, email.attempts), (OutboxEmail.Status.QUEUED, 0))

    def test_retry_ignores_emails_that_have_not_failed_and_offsite_next(self):
        email = outbox_row(status=OutboxEmail.Status.SENT, attempts=1)
        self.client.force_login(make_owner())
        response = self.client.post(reverse("console:email_retry", args=[email.pk]), {"next": "https://evil.example.com/"})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        email.refresh_from_db()
        self.assertEqual(email.status, OutboxEmail.Status.SENT)

    def test_staff_cannot_retry(self):
        email = outbox_row(status=OutboxEmail.Status.FAILED)
        self.client.force_login(make_staff())
        self.assertEqual(self.client.post(reverse("console:email_retry", args=[email.pk])).status_code, 403)
        email.refresh_from_db()
        self.assertEqual(email.status, OutboxEmail.Status.FAILED)


class ResendConfirmationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = make_store()
        cls.offering = make_offering(cls.store)
        cls.staff = make_staff()

    def setUp(self):
        self.client.force_login(self.staff)

    def order(self, status):
        return make_order(self.store, status=status, items=[(self.offering, 1)], buyer_email="pat@example.com")

    def detail(self, order):
        return self.client.get(reverse("console:order_detail", args=[order.order_number]))

    def test_paid_orders_show_the_button_and_it_queues_an_email(self):
        order = self.order(Order.Status.PAID)
        self.assertContains(self.detail(order), "Resend confirmation")
        self.assertContains(self.detail(order), "None sent for this order.")

        response = self.client.post(reverse("console:order_resend_confirmation", args=[order.order_number]))
        self.assertRedirects(response, reverse("console:order_detail", args=[order.order_number]), fetch_redirect_response=False)
        (email,) = OutboxEmail.objects.all()
        self.assertEqual((email.kind, email.to_email, email.related), ("order_confirmation", "pat@example.com", order))
        self.assertIn(order.order_number, email.text_body)

        page = self.detail(order)
        self.assertContains(page, "Confirmation email queued for pat@example.com")
        self.assertNotContains(page, "None sent for this order.")

    def test_other_orders_have_no_button_and_are_refused(self):
        for status in (Order.Status.PENDING, Order.Status.SENT_TO_OPS, Order.Status.CANCELLED):
            with self.subTest(status=status):
                order = self.order(status)
                self.assertNotContains(self.detail(order), "Resend confirmation")
                self.client.post(reverse("console:order_resend_confirmation", args=[order.order_number]))
        self.assertFalse(OutboxEmail.objects.exists())

    def test_signed_out_people_cannot_resend(self):
        order = self.order(Order.Status.PAID)
        self.client.logout()
        self.client.post(reverse("console:order_resend_confirmation", args=[order.order_number]))
        self.assertFalse(OutboxEmail.objects.exists())
