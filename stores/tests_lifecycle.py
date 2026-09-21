import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone as dj_timezone

from config import heartbeat
from stores.factories import TempRunDirMixin, make_staff, make_store

from .lifecycle import tick
from .models import Store, StoreStatusChange

NOW = datetime(2026, 10, 3, 22, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
S = Store.Status


class LifecycleTransitionTests(TestCase):
    def assertMoved(self, store, from_status, to_status):
        store.refresh_from_db()
        self.assertEqual(store.status, to_status)
        (change,) = store.status_changes.all()
        self.assertEqual((change.from_status, change.to_status), (from_status, to_status))
        self.assertEqual((change.reason, change.changed_by, change.changed_at), ("scheduled", None, NOW))

    def assertUntouched(self, store, status):
        store.refresh_from_db()
        self.assertEqual(store.status, status)
        self.assertFalse(store.status_changes.filter(reason="scheduled").exists())

    # --- transitions ---

    def test_scheduled_store_opens_when_its_time_comes(self):
        store = make_store(status=S.SCHEDULED, opens_at=NOW - HOUR, closes_at=NOW + 48 * HOUR)
        self.assertEqual(tick(NOW), (1, 0, 1))
        self.assertMoved(store, S.SCHEDULED, S.OPEN)

    def test_scheduled_store_with_no_close_time_still_opens(self):
        store = make_store(status=S.SCHEDULED, opens_at=NOW)  # exactly now counts
        self.assertEqual(tick(NOW), (1, 0, 1))
        self.assertMoved(store, S.SCHEDULED, S.OPEN)

    def test_open_store_closes_when_its_time_comes(self):
        store = make_store(status=S.OPEN, opens_at=NOW - 48 * HOUR, closes_at=NOW - HOUR)
        self.assertEqual(tick(NOW), (0, 1, 1))
        self.assertMoved(store, S.OPEN, S.CLOSED)

    def test_scheduled_store_past_both_times_goes_straight_to_closed(self):
        store = make_store(status=S.SCHEDULED, opens_at=NOW - 3 * HOUR, closes_at=NOW - HOUR)
        self.assertEqual(tick(NOW), (0, 1, 1))
        self.assertMoved(store, S.SCHEDULED, S.CLOSED)

    # --- non-transitions ---

    def test_drafts_are_never_touched(self):
        store = make_store(status=S.DRAFT, opens_at=NOW - 3 * HOUR, closes_at=NOW - HOUR)
        self.assertEqual(tick(NOW), (0, 0, 0))
        self.assertUntouched(store, S.DRAFT)

    def test_stores_without_dates_are_never_touched(self):
        scheduled = make_store(status=S.SCHEDULED)
        open_ = make_store(status=S.OPEN, opens_at=NOW - HOUR)  # opens_at alone doesn't close it
        self.assertEqual(tick(NOW), (0, 0, 0))
        self.assertUntouched(scheduled, S.SCHEDULED)
        self.assertUntouched(open_, S.OPEN)

    def test_open_store_before_its_close_time_stays_open(self):
        store = make_store(status=S.OPEN, opens_at=NOW - 3 * HOUR, closes_at=NOW + HOUR)
        self.assertEqual(tick(NOW), (0, 0, 1))
        self.assertUntouched(store, S.OPEN)

    def test_scheduled_store_before_its_open_time_stays_scheduled(self):
        store = make_store(status=S.SCHEDULED, opens_at=NOW + HOUR, closes_at=NOW + 48 * HOUR)
        self.assertEqual(tick(NOW), (0, 0, 1))
        self.assertUntouched(store, S.SCHEDULED)

    def test_closed_stores_stay_closed(self):
        store = make_store(status=S.CLOSED, opens_at=NOW - 3 * HOUR, closes_at=NOW - HOUR)
        self.assertEqual(tick(NOW), (0, 0, 0))
        self.assertUntouched(store, S.CLOSED)

    # --- staff win ---

    def manual(self, store, from_status, to_status, at):
        store.status = to_status
        store.save()
        StoreStatusChange.objects.create(
            store=store, from_status=from_status, to_status=to_status, changed_at=at,
            changed_by=make_staff(), reason=StoreStatusChange.Reason.MANUAL,
        )

    def test_a_store_reopened_by_staff_after_closing_stays_open(self):
        store = make_store(status=S.CLOSED, closes_at=NOW - 24 * HOUR)
        self.manual(store, S.CLOSED, S.OPEN, at=NOW - 2 * HOUR)
        self.assertEqual(tick(NOW), (0, 0, 1))
        self.assertUntouched(store, S.OPEN)

    def test_a_new_close_date_rearms_the_schedule(self):
        store = make_store(status=S.CLOSED, closes_at=NOW - 24 * HOUR)
        self.manual(store, S.CLOSED, S.OPEN, at=NOW - 2 * HOUR)
        Store.objects.filter(pk=store.pk).update(closes_at=NOW - HOUR)  # new date, after the reopen
        self.assertEqual(tick(NOW), (0, 1, 1))
        store.refresh_from_db()
        self.assertEqual(store.status, S.CLOSED)

    def test_a_store_held_back_by_staff_after_its_open_time_stays_scheduled(self):
        store = make_store(status=S.OPEN, opens_at=NOW - 3 * HOUR)
        self.manual(store, S.OPEN, S.SCHEDULED, at=NOW - 2 * HOUR)
        self.assertEqual(tick(NOW), (0, 0, 1))
        self.assertUntouched(store, S.SCHEDULED)

    def test_a_manual_change_before_the_scheduled_time_does_not_block_it(self):
        store = make_store(status=S.DRAFT, opens_at=NOW - HOUR)
        self.manual(store, S.DRAFT, S.SCHEDULED, at=NOW - 2 * HOUR)
        self.assertEqual(tick(NOW), (1, 0, 1))

    # --- safety ---

    def test_running_twice_changes_nothing_more(self):
        store = make_store(status=S.OPEN, closes_at=NOW - HOUR)
        make_store(status=S.SCHEDULED, opens_at=NOW - HOUR, closes_at=NOW + HOUR)
        self.assertEqual(tick(NOW), (1, 1, 2))
        self.assertEqual(tick(NOW), (0, 0, 1))  # the closed one has left the schedule
        self.assertEqual(StoreStatusChange.objects.count(), 2)
        self.assertEqual(store.status_changes.count(), 1)

    def test_each_store_is_locked_while_it_moves(self):
        make_store(status=S.OPEN, closes_at=NOW - HOUR)
        from django.db.models import QuerySet

        real = QuerySet.select_for_update
        with patch.object(QuerySet, "select_for_update", autospec=True, side_effect=real) as spy:
            tick(NOW)
        self.assertEqual(spy.call_count, 1)

    def test_a_store_moved_by_someone_else_meanwhile_is_left_alone(self):
        # Another run (or staff) closes the store between listing and locking.
        store = make_store(status=S.OPEN, closes_at=NOW - HOUR)
        from stores import lifecycle

        real_advance = lifecycle.advance

        def advance(pk, now):
            Store.objects.filter(pk=pk).update(status=S.CLOSED)
            return real_advance(pk, now)

        with patch("stores.lifecycle.advance", advance):
            self.assertEqual(tick(NOW), (0, 0, 1))
        self.assertFalse(store.status_changes.exists())


class LifecycleCommandTests(TempRunDirMixin, TestCase):
    def run_command(self):
        out = io.StringIO()
        call_command("lifecycle_tick", stdout=out)
        return out.getvalue()

    def test_prints_one_summary_line(self):
        now = dj_timezone.now()
        make_store(status=S.SCHEDULED, opens_at=now - HOUR)
        make_store(status=S.OPEN, closes_at=now + HOUR)
        make_store(status=S.DRAFT, opens_at=now - HOUR)
        self.assertEqual(self.run_command(), "lifecycle_tick: opened=1 closed=0 checked=2\n")

    def test_prints_the_line_even_when_there_is_nothing_to_do(self):
        self.assertEqual(self.run_command(), "lifecycle_tick: opened=0 closed=0 checked=0\n")

    def test_writes_the_heartbeat(self):
        self.assertIsNone(heartbeat.last_beat("lifecycle_tick"))
        before = dj_timezone.now() - timedelta(seconds=2)
        self.run_command()
        self.assertTrue((self.run_dir / "lifecycle_tick.heartbeat").exists())
        self.assertGreaterEqual(heartbeat.last_beat("lifecycle_tick"), before)

    def test_no_heartbeat_or_summary_when_the_run_fails(self):
        out = io.StringIO()
        with patch("stores.management.commands.lifecycle_tick.tick", side_effect=RuntimeError("database gone")):
            with self.assertRaises(RuntimeError):
                call_command("lifecycle_tick", stdout=out)
        self.assertEqual(out.getvalue(), "")
        self.assertIsNone(heartbeat.last_beat("lifecycle_tick"))

    def test_creates_the_run_dir_if_missing(self):
        with override_settings(RUN_DIR=str(self.run_dir / "not-yet")):
            self.run_command()
            self.assertIsNotNone(heartbeat.last_beat("lifecycle_tick"))
