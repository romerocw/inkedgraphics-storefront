from django.test import TestCase, override_settings

from stores.factories import make_order, make_store

from .models import OutboxEmail
from .outbox import emails_about, enqueue

INVITE_CONTEXT = {"invitee_name": "Dana", "inviter_name": "Charlie", "url": "https://example.com/invite/abc/"}


@override_settings(SITE_URL="https://shop.example.com", DEFAULT_FROM_EMAIL="Shop <shop@example.com>")
class EnqueueTests(TestCase):
    def test_renders_text_and_html_and_queues_one_row(self):
        email = enqueue("invitation", "dana@example.com", "Welcome", "console/email/invitation", INVITE_CONTEXT)

        self.assertEqual(OutboxEmail.objects.count(), 1)
        self.assertEqual(email.status, OutboxEmail.Status.QUEUED)
        self.assertEqual((email.kind, email.to_email, email.to_name), ("invitation", "dana@example.com", ""))
        self.assertEqual(email.subject, "Welcome")
        self.assertEqual(email.from_email, "Shop <shop@example.com>")
        self.assertEqual(email.attempts, 0)
        self.assertIsNone(email.sent_at)
        self.assertIn("Hi Dana", email.text_body)
        self.assertIn("https://example.com/invite/abc/", email.text_body)
        self.assertNotIn("<p", email.text_body)
        self.assertIn("<html", email.html_body)
        self.assertIn("Hi Dana", email.html_body)

    def test_site_url_comes_from_settings(self):
        email = enqueue("invitation", "dana@example.com", "Welcome", "console/email/invitation", INVITE_CONTEXT)
        self.assertIn("https://shop.example.com", email.text_body)
        self.assertIn("https://shop.example.com", email.html_body)

    def test_accepts_a_name_and_address_pair(self):
        email = enqueue("invitation", ("Dana Ruiz", "dana@example.com"), "Welcome", "console/email/invitation", INVITE_CONTEXT)
        self.assertEqual((email.to_name, email.to_email), ("Dana Ruiz", "dana@example.com"))

    def test_links_to_the_record_it_is_about(self):
        order = make_order(make_store())
        email = enqueue("invitation", "dana@example.com", "Hi", "console/email/invitation", INVITE_CONTEXT, related=order)
        enqueue("invitation", "dana@example.com", "Unrelated", "console/email/invitation", INVITE_CONTEXT)

        self.assertEqual(OutboxEmail.objects.get(pk=email.pk).related, order)
        self.assertEqual(list(emails_about(order)), [email])
