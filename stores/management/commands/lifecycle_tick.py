from django.core.management.base import BaseCommand

from config import heartbeat
from stores.lifecycle import tick


class Command(BaseCommand):
    help = "Open scheduled stores and close open ones whose time has come. Run by cron every 5 minutes."

    def handle(self, *args, **options):
        opened, closed, checked = tick()
        # Printed on every run, even when nothing changed: CloudWatch alarms if it stops.
        self.stdout.write(f"lifecycle_tick: opened={opened} closed={closed} checked={checked}")
        heartbeat.beat("lifecycle_tick")
