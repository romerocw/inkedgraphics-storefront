import contextlib
import importlib
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from django.apps import apps
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from console.forms import StoreForm
from stores.factories import make_client, make_staff, make_store

from .models import Store, StoreStatusChange

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")

rezone_migration = importlib.import_module("stores.migrations.0005_store_time_zone")


def render(template, **context):
    return Template(template).render(Context(context))


class StoreTimeZoneDisplayTests(TestCase):
    def test_times_show_on_the_store_clock_with_its_zone(self):
        store = make_store(time_zone="America/Los_Angeles", closes_at=datetime(2026, 10, 4, 1, 0, tzinfo=UTC))
        self.assertEqual(render('{{ s.closes_local|date:"F j, Y, g:i a T" }}', s=store), "October 3, 2026, 6:00 p.m. PDT")

    def test_standard_time_is_labelled_as_such(self):
        store = make_store(time_zone="America/Los_Angeles", opens_at=datetime(2026, 12, 1, 17, 0, tzinfo=UTC))
        self.assertEqual(render('{{ s.opens_local|date:"g:i a T" }}', s=store), "9:00 a.m. PST")

    def test_blank_times_stay_blank(self):
        store = make_store()
        self.assertIsNone(store.opens_local)
        self.assertEqual(render('{{ s.closes_local|date:"M j"|default:"Not set" }}', s=store), "Not set")

    def test_buyer_store_page_shows_the_store_time(self):
        store = make_store(time_zone="America/Chicago", closes_at=datetime(2026, 10, 3, 23, 0, tzinfo=UTC))
        self.assertContains(self.client.get(f"/{store.slug}/"), "Store closes October 3, 2026, 6:00 p.m. CDT.")

    def test_buyer_page_for_a_scheduled_store_shows_the_opening_time(self):
        store = make_store(status=Store.Status.SCHEDULED, opens_at=datetime(2026, 10, 1, 13, 0, tzinfo=UTC))
        self.assertContains(self.client.get(f"/{store.slug}/"), "This store opens October 1, 2026, 9:00 a.m. EDT.")


class StoreFormTimeZoneTests(TestCase):
    def setUp(self):
        self.shop_client = make_client()

    def data(self, **overrides):
        return {
            "client": self.shop_client.pk, "name": "Fall Store", "status": "scheduled",
            "time_zone": "America/Los_Angeles", "opens_at": "2026-10-01T09:00", "closes_at": "2026-10-03T18:00",
            "primary_color": "", "subdomain": "", **overrides,
        }

    def test_typed_times_are_read_in_the_store_zone(self):
        form = StoreForm(self.data())
        self.assertTrue(form.is_valid(), form.errors)
        store = form.save()
        self.assertEqual(store.opens_at, datetime(2026, 10, 1, 9, 0, tzinfo=PACIFIC))
        self.assertEqual(store.closes_at, datetime(2026, 10, 4, 1, 0, tzinfo=UTC))

    def test_the_zone_picked_in_the_same_form_is_the_one_used(self):
        form = StoreForm(self.data(time_zone="America/New_York"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().closes_at, datetime(2026, 10, 3, 22, 0, tzinfo=UTC))

    def test_editing_shows_saved_times_on_the_store_clock(self):
        store = make_store(time_zone="America/Denver", opens_at=datetime(2026, 10, 1, 15, 30, tzinfo=UTC))
        html = str(StoreForm(instance=store)["opens_at"])
        self.assertIn('value="2026-10-01T09:30"', html)

    def test_editing_without_touching_the_times_keeps_them(self):
        store = make_store(client=self.shop_client, time_zone="America/Denver", opens_at=datetime(2026, 10, 1, 15, 30, tzinfo=UTC))
        form = StoreForm(self.data(time_zone="America/Denver", opens_at="2026-10-01T09:30", closes_at=""), instance=store)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().opens_at, datetime(2026, 10, 1, 15, 30, tzinfo=UTC))

    def test_a_time_the_clocks_skip_is_refused(self):
        # 2:30 am on 8 March 2026 never happens in Pacific time (clocks go 1:59 -> 3:00).
        form = StoreForm(self.data(opens_at="2026-03-08T02:30", closes_at="2026-03-20T18:00"))
        self.assertFalse(form.is_valid())
        self.assertIn("clocks skip it", form.errors["opens_at"][0])

    def test_arizona_has_no_skipped_times(self):
        form = StoreForm(self.data(time_zone="America/Phoenix", opens_at="2026-03-08T02:30", closes_at="2026-03-20T18:00"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_closing_before_opening_is_refused(self):
        form = StoreForm(self.data(opens_at="2026-10-03T18:00", closes_at="2026-10-03T09:00"))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["closes_at"], ["The store has to close after it opens."])

    def test_dates_are_optional(self):
        form = StoreForm(self.data(status="draft", opens_at="", closes_at=""))
        self.assertTrue(form.is_valid(), form.errors)
        store = form.save()
        self.assertIsNone(store.opens_at)
        self.assertEqual(store.time_zone, "America/Los_Angeles")


class RezoneMigrationTests(TestCase):
    """0005 keeps the wall-clock time staff typed while the site ran on UTC, read as Eastern."""

    def test_rezone_keeps_the_wall_clock_time(self):
        six_pm_utc = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
        self.assertEqual(rezone_migration.rezone(six_pm_utc, UTC, EASTERN), datetime(2026, 10, 3, 18, 0, tzinfo=EASTERN))
        self.assertIsNone(rezone_migration.rezone(None, UTC, EASTERN))

    def test_forwards_and_backwards(self):
        store = make_store(opens_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC), closes_at=datetime(2026, 12, 3, 18, 0, tzinfo=UTC))
        untouched = make_store()
        updated_at = store.updated_at

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rezone_migration.utc_to_eastern(apps, None)
        store.refresh_from_db()
        self.assertEqual(store.opens_at, datetime(2026, 10, 1, 9, 0, tzinfo=EASTERN))  # EDT
        self.assertEqual(store.closes_at, datetime(2026, 12, 3, 18, 0, tzinfo=EASTERN))  # EST
        self.assertEqual(store.updated_at, updated_at)
        self.assertIn(f"store {store.pk} ({store.name})", out.getvalue())
        self.assertNotIn(f"store {untouched.pk} ", out.getvalue())

        with contextlib.redirect_stdout(io.StringIO()):
            rezone_migration.eastern_to_utc(apps, None)
        store.refresh_from_db()
        self.assertEqual(store.opens_at, datetime(2026, 10, 1, 9, 0, tzinfo=UTC))


class ManualStatusLogTests(TestCase):
    """Status changes made in the console store form are logged with who made them."""

    def setUp(self):
        self.user = make_staff()
        self.client.force_login(self.user)
        self.shop_client = make_client()

    def post(self, url, **overrides):
        data = {
            "client": self.shop_client.pk, "name": "Fall Store", "status": "draft", "time_zone": "America/New_York",
            "opens_at": "", "closes_at": "", "primary_color": "", "subdomain": "", **overrides,
        }
        return self.client.post(url, data)

    def test_creating_a_store_logs_its_first_status(self):
        self.post(reverse("console:store_new"), status="scheduled")
        (change,) = StoreStatusChange.objects.all()
        self.assertEqual((change.from_status, change.to_status), ("", "scheduled"))
        self.assertEqual((change.reason, change.changed_by), (StoreStatusChange.Reason.MANUAL, self.user))

    def test_changing_status_logs_from_and_to(self):
        store = make_store(client=self.shop_client, status=Store.Status.CLOSED)
        response = self.post(reverse("console:store_edit", args=[store.pk]), status="open")
        self.assertEqual(response.status_code, 302)
        (change,) = store.status_changes.all()
        self.assertEqual((change.from_status, change.to_status, change.changed_by), ("closed", "open", self.user))

    def test_saving_without_changing_status_logs_nothing(self):
        store = make_store(client=self.shop_client, status=Store.Status.OPEN)
        self.post(reverse("console:store_edit", args=[store.pk]), status="open", name="Renamed")
        self.assertFalse(store.status_changes.exists())
