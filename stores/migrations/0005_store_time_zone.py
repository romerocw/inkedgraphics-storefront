"""Give each store a time zone, and keep the open/close times staff typed.

Until now the site ran on UTC, so a close time typed as "6:00 pm" was saved as 6 pm UTC.
Staff meant their own local time, so this keeps the wall-clock time they typed and reads
it as Eastern (every existing store's new time zone). Stores really in another zone need
their zone set by hand afterwards; each change is printed to make that easy.
"""

from datetime import timezone
from zoneinfo import ZoneInfo

from django.db import migrations, models

EASTERN = ZoneInfo("America/New_York")
TIME_ZONES = [
    ("America/New_York", "Eastern"),
    ("America/Chicago", "Central"),
    ("America/Denver", "Mountain"),
    ("America/Phoenix", "Arizona (no daylight saving)"),
    ("America/Los_Angeles", "Pacific"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
]


def rezone(value, typed_in, meant_in):
    """Same wall-clock time, different zone: 6 pm UTC -> 6 pm Eastern."""
    return value.astimezone(typed_in).replace(tzinfo=None).replace(tzinfo=meant_in) if value else value


def _move(apps, typed_in, meant_in, verb):
    Store = apps.get_model("stores", "Store")
    for store in Store.objects.exclude(opens_at=None, closes_at=None).order_by("pk"):
        changes = {f: rezone(getattr(store, f), typed_in, meant_in) for f in ("opens_at", "closes_at")}
        Store.objects.filter(pk=store.pk).update(**changes)  # update() leaves updated_at alone
        print(
            f"\n  {verb} store {store.pk} ({store.name}): "
            + ", ".join(f"{f} {getattr(store, f)} -> {v}" for f, v in changes.items() if v),
            end="",
        )


def utc_to_eastern(apps, schema_editor):
    _move(apps, timezone.utc, EASTERN, "Re-read as Eastern:")


def eastern_to_utc(apps, schema_editor):
    _move(apps, EASTERN, timezone.utc, "Back to UTC:")


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0004_client_logo"),
    ]

    operations = [
        migrations.AddField(
            model_name="store",
            name="time_zone",
            field=models.CharField(
                choices=TIME_ZONES, default="America/New_York", max_length=40,
                help_text="The store's local time zone. Open and close times are typed and shown in it.",
            ),
        ),
        migrations.AlterField(
            model_name="store",
            name="opens_at",
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="When a scheduled store opens by itself, in the store's time zone. Blank = open it by hand.",
            ),
        ),
        migrations.AlterField(
            model_name="store",
            name="closes_at",
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="When an open store closes by itself, in the store's time zone. Blank = close it by hand.",
            ),
        ),
        migrations.RunPython(utc_to_eastern, eastern_to_utc),
    ]
