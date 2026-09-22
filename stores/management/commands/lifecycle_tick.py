from django.core.management.base import BaseCommand

from config import heartbeat
from stores.lifecycle import tick
from stores.services.share_kit import refresh_open_stores


class Command(BaseCommand):
    help = "Open scheduled stores and close open ones whose time has come. Run by cron every 5 minutes."

    def handle(self, *args, **options):
        opened, closed, checked = tick()
        # After the moves, so a store that just opened gets its share kit on the same run.
        # refresh_open_stores() swallows its own failures: the status job is what the alarm
        # watches, and a store with a broken logo must not look like a dead cron.
        built, failed = refresh_open_stores()
        # Printed on every run, even when nothing changed: CloudWatch alarms if it stops.
        self.stdout.write(
            f"lifecycle_tick: opened={opened} closed={closed} checked={checked} "
            f"kits_built={built} kits_failed={failed}"
        )
        heartbeat.beat("lifecycle_tick")
