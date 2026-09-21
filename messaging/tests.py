import io
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.db.models import QuerySet
from django.test import TestCase, override_settings
from django.utils import timezone

from config import heartbeat
from stores.factories import TempRunDirMixin, make_order, make_store

from .models import OutboxEmail
from .outbox import BACKOFF, MAX_ATTEMPTS, claim, emails_about, enqueue, retry, send_due

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


def queue(n=1, **kwargs):
    rows = []
    for i in range(n):
        fields = {"kind": "test", "to_email": f"buyer{i}@example.com", "subject": f"Email {i}", "text_body": "Hello", "html_body": "<p>Hello</p>"}
        rows.append(OutboxEmail.objects.create(**{**fields, **kwargs}))
    return rows


def run_command(**options):
    out = io.StringIO()
    call_command("send_outbox", stdout=out, **options)
    return out.getvalue().strip()


class SendOutboxTests(TempRunDirMixin, TestCase):
    def test_writes_its_heartbeat(self):
        run_command()
        self.assertIsNotNone(heartbeat.last_beat("send_outbox"))

    def test_sends_queued_email_and_prints_a_summary(self):
        (email,) = queue(to_name="Dana Ruiz", to_email="dana@example.com")

        self.assertEqual(run_command(), "send_outbox: sent=1 failed=0 remaining=0")

        email.refresh_from_db()
        self.assertEqual(email.status, OutboxEmail.Status.SENT)
        self.assertEqual(email.attempts, 1)
        self.assertIsNotNone(email.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["Dana Ruiz <dana@example.com>"])
        self.assertEqual(message.subject, "Email 0")
        self.assertEqual(message.body, "Hello")
        self.assertEqual(message.alternatives[0].content, "<p>Hello</p>")

    def test_does_nothing_quietly_when_empty(self):
        self.assertEqual(run_command(), "send_outbox: sent=0 failed=0 remaining=0")
        self.assertEqual(mail.outbox, [])

    def test_sends_at_most_the_limit_oldest_first(self):
        rows = queue(52)
        self.assertEqual(run_command(), "send_outbox: sent=50 failed=0 remaining=2")
        self.assertEqual([m.subject for m in mail.outbox], [r.subject for r in rows[:50]])
        self.assertEqual(run_command(limit=10), "send_outbox: sent=2 failed=0 remaining=0")

    def test_never_resends_sent_email(self):
        queue()
        run_command()
        self.assertEqual(run_command(), "send_outbox: sent=0 failed=0 remaining=0")
        self.assertEqual(len(mail.outbox), 1)

    def test_records_a_failure_and_waits_before_retrying(self):
        (email,) = queue()
        with patch("django.core.mail.EmailMessage.send", side_effect=ConnectionRefusedError("SMTP down")):
            before = timezone.now()
            self.assertEqual(run_command(), "send_outbox: sent=0 failed=1 remaining=0")

        email.refresh_from_db()
        self.assertEqual(email.status, OutboxEmail.Status.FAILED)
        self.assertEqual(email.attempts, 1)
        self.assertEqual(email.last_error, "ConnectionRefusedError: SMTP down")
        self.assertGreaterEqual(email.next_attempt_at, before + BACKOFF[0])
        self.assertIsNone(email.sent_at)

        # Not due yet, so the next run leaves it alone.
        self.assertEqual(run_command(), "send_outbox: sent=0 failed=0 remaining=0")
        self.assertEqual(mail.outbox, [])

        # Once the wait is over it goes, and the error is cleared.
        with patch("django.utils.timezone.now", return_value=email.next_attempt_at + timedelta(seconds=1)):
            self.assertEqual(run_command(), "send_outbox: sent=1 failed=0 remaining=0")
        email.refresh_from_db()
        self.assertEqual((email.status, email.attempts, email.last_error), (OutboxEmail.Status.SENT, 2, ""))

    def test_backs_off_longer_each_time_then_gives_up_after_five(self):
        (email,) = queue()
        waits = []
        with patch("django.core.mail.EmailMessage.send", side_effect=OSError("nope")):
            for attempt in range(1, MAX_ATTEMPTS + 1):
                now = timezone.now() + timedelta(days=attempt)  # always past any wait
                with patch("django.utils.timezone.now", return_value=now):
                    self.assertEqual(run_command(), "send_outbox: sent=0 failed=1 remaining=0")
                email.refresh_from_db()
                self.assertEqual(email.attempts, attempt)
                if email.next_attempt_at:
                    waits.append(email.next_attempt_at - now)

            self.assertEqual(waits, BACKOFF)
            self.assertIsNone(email.next_attempt_at)
            self.assertTrue(email.gave_up)

            # Given up: no more attempts, however long we wait.
            with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(days=30)):
                self.assertEqual(run_command(), "send_outbox: sent=0 failed=0 remaining=0")
        email.refresh_from_db()
        self.assertEqual(email.attempts, MAX_ATTEMPTS)

    def test_one_failure_does_not_stop_the_rest(self):
        bad, good = queue(2)
        real_send = mail.EmailMessage.send

        def send(message, *args, **kwargs):
            if message.subject == bad.subject:
                raise OSError("bounced")
            return real_send(message, *args, **kwargs)

        with patch("django.core.mail.EmailMessage.send", send):
            self.assertEqual(run_command(), "send_outbox: sent=1 failed=1 remaining=0")
        self.assertEqual([m.subject for m in mail.outbox], [good.subject])

    def test_each_row_is_locked_while_it_is_sent(self):
        # SQLite can't lock rows, so check the query asks for the lock; MariaDB honours it.
        queue(2)
        real = QuerySet.select_for_update
        with patch.object(QuerySet, "select_for_update", autospec=True, side_effect=real) as spy:
            send_due()
        self.assertEqual(spy.call_count, 2)
        for call in spy.call_args_list:
            self.assertEqual(call.kwargs, {"skip_locked": connection.features.has_select_for_update_skip_locked})

    def test_skips_a_row_another_run_sent_first(self):
        # Simulates two overlapping runs: this run has already listed both rows when the
        # other run sends the second one. The lock + re-check must stop a second send.
        first, second = queue(2)
        real_send = mail.EmailMessage.send

        def send(message, *args, **kwargs):
            if message.subject == first.subject:
                OutboxEmail.objects.filter(pk=second.pk).update(status=OutboxEmail.Status.SENT, sent_at=timezone.now())
            return real_send(message, *args, **kwargs)

        with patch("django.core.mail.EmailMessage.send", send):
            self.assertEqual(send_due(), (1, 0, 0))
        self.assertEqual([m.subject for m in mail.outbox], [first.subject])
        second.refresh_from_db()
        self.assertEqual(second.attempts, 0)

    def test_retry_requeues_a_failed_email_with_fresh_attempts(self):
        (email,) = queue(status=OutboxEmail.Status.FAILED, attempts=MAX_ATTEMPTS, last_error="nope")
        retry(email)
        self.assertEqual(run_command(), "send_outbox: sent=1 failed=0 remaining=0")
        email.refresh_from_db()
        self.assertEqual((email.status, email.attempts), (OutboxEmail.Status.SENT, 1))


class CronFileTests(TestCase):
    """deploy/cron.d/storefront is installed as-is by deploy.sh; cron is unforgiving about it."""

    def setUp(self):
        from django.conf import settings

        self.text = (settings.BASE_DIR / "deploy" / "cron.d" / "storefront").read_text()

    def test_ends_with_a_newline(self):
        # cron silently drops a final line that has no newline.
        self.assertTrue(self.text.endswith("\n"))

    def jobs(self):
        return [line for line in self.text.splitlines() if line and not line.startswith("#") and "=" not in line.split()[0]]

    def assertJob(self, command, schedule):
        (job,) = [j for j in self.jobs() if f"manage.py {command} " in j]
        fields = job.split()
        self.assertEqual(fields[:6], [*schedule.split(), "storefront"])
        self.assertEqual(fields[6:9], ["flock", "-n", f"/srv/storefront/run/{command}.lock"])
        self.assertIn(f"/srv/storefront/venv/bin/python /srv/storefront/app/manage.py {command} >>", job)
        self.assertTrue(job.endswith(f">> /srv/storefront/logs/cron-{command}.log 2>&1"))

    def test_runs_send_outbox_every_minute(self):
        self.assertJob("send_outbox", "* * * * *")

    def test_runs_lifecycle_tick_every_five_minutes(self):
        self.assertJob("lifecycle_tick", "*/5 * * * *")

    def test_has_no_other_jobs(self):
        self.assertEqual(len(self.jobs()), 2)

    def test_logrotate_covers_every_cron_log(self):
        from django.conf import settings

        rotate = (settings.BASE_DIR / "deploy" / "logrotate.d" / "storefront").read_text()
        self.assertIn("/srv/storefront/logs/cron-*.log {", rotate)


class CloudWatchConfigTests(TestCase):
    def test_ships_the_app_web_and_cron_logs_to_one_group_for_30_days(self):
        import json

        from django.conf import settings

        config = json.loads((settings.BASE_DIR / "deploy" / "cloudwatch-agent.json").read_text())
        files = config["logs"]["logs_collected"]["files"]["collect_list"]
        self.assertEqual(
            [f["file_path"].removeprefix("/srv/storefront/logs/") for f in files],
            ["uwsgi.log", "httpd-error.log", "cron-send_outbox.log", "cron-lifecycle_tick.log"],
        )
        self.assertEqual({f["log_group_name"] for f in files}, {"/storefront/prod"})
        self.assertEqual({f["retention_in_days"] for f in files}, {30})
