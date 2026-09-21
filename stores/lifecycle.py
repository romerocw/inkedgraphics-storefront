"""Store status over time: the schedule (lifecycle_tick) and staff both move stores along.

Every move is logged as a StoreStatusChange. A staff change made after the scheduled moment
wins over the schedule; setting a new date re-arms it.
"""

from .models import StoreStatusChange


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
