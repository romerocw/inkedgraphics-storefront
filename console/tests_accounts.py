from django.core import mail
from django.test import TestCase

from console.mail import send_console_email


class ConsoleEmailTests(TestCase):
    def test_sends_a_plain_text_and_html_pair_from_the_shop_address(self):
        send_console_email(
            "Test subject",
            "invitation",
            {"invitee_name": "Dana", "inviter_name": "Charlie", "url": "https://example.com/invite/abc/", "site_url": "https://example.com"},
            "dana@example.com",
        )
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Test subject")
        self.assertEqual(message.to, ["dana@example.com"])
        self.assertEqual(message.from_email, "Inked Graphics Stores <orders@inkedgraphics.com>")
        self.assertIn("https://example.com/invite/abc/", message.body)
        self.assertNotIn("<", message.body.split("https://")[0])  # the text part stays plain

        html, content_type = message.alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn("Inked Graphics", html)
        self.assertIn("https://example.com/invite/abc/", html)
