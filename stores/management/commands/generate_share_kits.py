from django.core.management.base import BaseCommand, CommandError

from stores.models import Store
from stores.services import share_kit


class Command(BaseCommand):
    help = (
        "Build the share kit (QR, flyer, social image) for open stores whose details changed. "
        "lifecycle_tick does this every 5 minutes; run it here to force one or backfill."
    )

    def add_arguments(self, parser):
        parser.add_argument("--store", help="Slug of a single store, in any state from open onward.")
        parser.add_argument("--force", action="store_true", help="Rebuild even if nothing changed.")

    def handle(self, *args, **options):
        if options["store"]:
            store = Store.objects.filter(slug=options["store"]).select_related("client").first()
            if not store:
                raise CommandError(f"No store with slug {options['store']!r}.")
            built = share_kit.generate(store, force=options["force"])
            self.stdout.write(f"generate_share_kits: {store.slug} {'built' if built else 'unchanged'}")
            return

        if options["force"]:
            # A forced sweep is a deliberate backfill, so it covers closed stores too.
            stores = Store.objects.filter(status__in=[Store.Status.OPEN, Store.Status.CLOSED])
            built = sum(bool(share_kit.generate(s, force=True)) for s in stores.select_related("client"))
            failed = 0
        else:
            built, failed = share_kit.refresh_open_stores()
        self.stdout.write(f"generate_share_kits: built={built} failed={failed}")
