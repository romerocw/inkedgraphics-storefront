import os
from datetime import datetime, timedelta, timezone

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone

from config import heartbeat
from messaging.models import OutboxEmail
from stores.factories import TempRunDirMixin, make_manager, make_owner, make_staff, make_store
from stores.lifecycle import schedule_notes
from stores.models import Store, StoreStatusChange

UTC = timezone.utc
HOUR = timedelta(hours=1)
S = Store.Status


class ScheduleOnStorePageTests(TestCase):
    def setUp(self):
        self.staffer = make_staff(first_name="Dana", last_name="Ruiz")
        self.client.force_login(self.staffer)

    def page(self, store):
        return self.client.get(reverse("console:store_detail", args=[store.pk]))

    def test_scheduled_store_shows_both_times_in_its_zone(self):
        store = make_store(
            status=S.SCHEDULED, time_zone="America/Los_Angeles",
            opens_at=datetime(2030, 10, 1, 16, 0, tzinfo=UTC), closes_at=datetime(2030, 10, 4, 1, 0, tzinfo=UTC),
        )
        page = self.page(store)
        self.assertContains(page, "Opens automatically on Oct 1, 2030, 9:00 a.m. PDT.")
        self.assertContains(page, "Closes automatically on Oct 3, 2030, 6:00 p.m. PDT.")
        self.assertContains(page, "Pacific")  # summary tab time zone row

    def test_open_store_shows_only_the_close(self):
        store = make_store(status=S.OPEN, opens_at=datetime(2020, 1, 1, tzinfo=UTC), closes_at=datetime(2030, 10, 3, 22, 0, tzinfo=UTC))
        page = self.page(store)
        self.assertContains(page, "Closes automatically on Oct 3, 2030, 6:00 p.m. EDT.")
        self.assertNotContains(page, "Opens automatically")

    def test_no_schedule_text_without_dates_or_for_drafts_and_closed_stores(self):
        for store in (
            make_store(status=S.OPEN),
            make_store(status=S.DRAFT, opens_at=dj_timezone.now() + HOUR),
            make_store(status=S.CLOSED, closes_at=dj_timezone.now() + HOUR),
        ):
            with self.subTest(status=store.status):
                self.assertNotContains(self.page(store), "automatically")

    def test_a_store_kept_open_by_staff_says_so(self):
        now = dj_timezone.now()
        store = make_store(status=S.OPEN, closes_at=now - 24 * HOUR)
        StoreStatusChange.objects.create(store=store, from_status=S.CLOSED, to_status=S.OPEN, changed_by=self.staffer, reason="manual", changed_at=now - HOUR)
        self.assertEqual([n["state"] for n in schedule_notes(store, now)], ["held"])
        self.assertContains(self.page(store), "staff changed the status by hand")

    def test_a_time_just_passed_is_due(self):
        now = dj_timezone.now()
        store = make_store(status=S.OPEN, closes_at=now - HOUR)
        self.assertEqual([n["state"] for n in schedule_notes(store, now)], ["due"])

    def test_summary_shows_status_history(self):
        store = make_store(status=S.OPEN)
        StoreStatusChange.objects.create(store=store, from_status=S.SCHEDULED, to_status=S.OPEN, reason="scheduled")
        StoreStatusChange.objects.create(store=store, from_status=S.OPEN, to_status=S.CLOSED, reason="manual", changed_by=self.staffer)
        page = self.page(store)
        history = " ".join(page.content.decode().split("Status history")[1].split())
        self.assertIn("Scheduled &rarr; open, on schedule", history)
        self.assertIn("Open &rarr; closed, by Dana Ruiz", history)
        self.assertContains(page, "by Dana Ruiz")


class SystemBoxTests(TempRunDirMixin, TestCase):
    url = reverse("console:dashboard")

    def test_owners_and_managers_see_fresh_jobs_in_green(self):
        heartbeat.beat("lifecycle_tick")
        heartbeat.beat("send_outbox")
        for user in (make_owner(), make_manager()):
            self.client.force_login(user)
            page = self.client.get(self.url)
            self.assertContains(page, "System")
            self.assertContains(page, "Store schedule")
            self.assertEqual(page.context["system"]["jobs"][0]["stale"], False)
            self.assertNotContains(page, "check cron")

    def test_old_or_missing_heartbeats_are_red(self):
        heartbeat.beat("lifecycle_tick")
        old = (dj_timezone.now() - timedelta(minutes=16)).timestamp()
        os.utime(heartbeat.path("lifecycle_tick"), (old, old))
        self.client.force_login(make_owner())
        page = self.client.get(self.url)
        jobs = {j["name"]: j for j in page.context["system"]["jobs"]}
        self.assertTrue(jobs["lifecycle_tick"]["stale"])
        self.assertIsNone(jobs["send_outbox"]["last"])
        self.assertContains(page, "Has never run on this server")
        self.assertContains(page, "bg-red-50", count=2)

    def test_shows_the_email_backlog(self):
        base = {"kind": "t", "to_email": "a@example.com", "subject": "s", "text_body": "b"}
        OutboxEmail.objects.create(**base)
        OutboxEmail.objects.create(**base, status=OutboxEmail.Status.FAILED, attempts=5)
        self.client.force_login(make_owner())
        system = self.client.get(self.url).context["system"]
        self.assertEqual((system["emails_waiting"], system["emails_given_up"]), (1, 1))

    def test_staff_do_not_see_it(self):
        self.client.force_login(make_staff())
        page = self.client.get(self.url)
        self.assertNotIn("system", page.context)
        self.assertNotContains(page, "Store schedule")
