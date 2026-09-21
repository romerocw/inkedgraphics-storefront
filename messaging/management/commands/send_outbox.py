from django.core.management.base import BaseCommand

from config import heartbeat
from messaging.outbox import BATCH_SIZE, send_due


class Command(BaseCommand):
    help = "Send queued outbox emails (and retry failed ones that are due). Run by cron every minute."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=BATCH_SIZE, help=f"Most emails to send this run (default {BATCH_SIZE}).")

    def handle(self, *args, limit, **options):
        sent, failed, remaining = send_due(limit)
        self.stdout.write(f"send_outbox: sent={sent} failed={failed} remaining={remaining}")
        heartbeat.beat("send_outbox")
