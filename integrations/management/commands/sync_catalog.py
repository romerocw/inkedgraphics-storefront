from django.core.management.base import BaseCommand

from integrations.catalog_sync import sync_catalog
from integrations.models import CatalogSyncState
from integrations.ops_client import OpsClient, OpsError


class Command(BaseCommand):
    help = (
        "Pull the ops catalog into Blank/BlankVariant (docs/ops-storefront-api.md §4). "
        "Incremental by default; --full ignores the stored cursor."
    )

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Sync everything, ignoring the cursor.")

    def handle(self, *args, **options):
        client = OpsClient()
        if not client.is_configured:
            # Not an error: no ops link is the normal state on a machine that hasn't been given one.
            self.stdout.write("sync_catalog: skipped, OPS_API_URL/OPS_API_TOKEN not set")
            return

        try:
            result = sync_catalog(client=client, full=options["full"])
        except OpsError as exc:
            state = CatalogSyncState.load()
            state.last_error = str(exc)[:500]
            state.save()
            # The cursor was never moved, so the next run repeats this work, which is safe.
            self.stderr.write(f"sync_catalog: failed ({exc}) — cursor left at {state.cursor or 'never'}")
            raise SystemExit(1)

        self.stdout.write(f"sync_catalog: {result.summary}")
        for row in result.deactivated:
            self.stdout.write(f"  now inactive: {row}")
