"""Store status over time: the schedule (lifecycle_tick) and staff both move stores along.

Every move is logged as a StoreStatusChange. A staff change made after the scheduled moment
wins over the schedule; setting a new date re-arms it.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Store, StoreStatusChange


def record_manual_change(store, from_status, user):
    """Log a status change made in the console store form. No-op if the status didn't change."""
    if from_status == store.status:
        return None
    return StoreStatusChange.objects.create(
        store=store,
        from_status=from_status,
        to_status=store.status,
        changed_by=user if user and user.is_authenticated else None,
        reason=StoreStatusChange.Reason.MANUAL,
    )


# --- the schedule (manage.py lifecycle_tick, run by cron every 5 minutes) ---

def on_schedule():
    """Stores the schedule looks after: scheduled with an open time, or open with a close time.

    Drafts, closed stores and stores without the relevant date are never touched.
    """
    return Store.objects.filter(
        Q(status=Store.Status.SCHEDULED, opens_at__isnull=False) | Q(status=Store.Status.OPEN, closes_at__isnull=False)
    )


def next_move(store, now):
    """(new status, the scheduled moment that calls for it), or None if nothing is due."""
    if store.status == Store.Status.SCHEDULED and store.opens_at and store.opens_at <= now:
        # Both times already passed (e.g. the job was down): go straight to closed rather
        # than opening for a moment.
        if store.closes_at and store.closes_at <= now:
            return Store.Status.CLOSED, store.closes_at
        return Store.Status.OPEN, store.opens_at
    if store.status == Store.Status.OPEN and store.closes_at and store.closes_at <= now:
        return Store.Status.CLOSED, store.closes_at
    return None


def staff_overrode(store, since):
    """Did staff change this store's status by hand at or after the scheduled moment?"""
    return store.status_changes.filter(reason=StoreStatusChange.Reason.MANUAL, changed_at__gte=since).exists()


def advance(store_pk, now):
    """Apply the schedule to one store. Returns the new status, or None if nothing changed.

    The row is locked and re-read, so an overlapping run or a staff edit in progress can't
    lead to a double move or a double log entry.
    """
    with transaction.atomic():
        store = on_schedule().select_for_update().filter(pk=store_pk).first()
        move = store and next_move(store, now)
        if not move:
            return None
        to_status, moment = move
        if staff_overrode(store, moment):
            return None
        Store.objects.filter(pk=store.pk).update(status=to_status, updated_at=now)
        StoreStatusChange.objects.create(
            store=store, from_status=store.status, to_status=to_status, changed_at=now,
            reason=StoreStatusChange.Reason.SCHEDULED,
        )
        return to_status


def tick(now=None):
    """Open and close every store that's due. Returns (opened, closed, checked)."""
    now = now or timezone.now()
    stores = on_schedule()
    checked = stores.count()
    due = stores.filter(
        Q(status=Store.Status.SCHEDULED, opens_at__lte=now) | Q(status=Store.Status.OPEN, closes_at__lte=now)
    )
    opened = closed = 0
    for pk in due.order_by("pk").values_list("pk", flat=True):
        moved_to = advance(pk, now)
        opened += moved_to == Store.Status.OPEN
        closed += moved_to == Store.Status.CLOSED
    return opened, closed, checked
