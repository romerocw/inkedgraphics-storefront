"""Pull the ops catalog into catalog.Blank / BlankVariant (API A, §4).

The procedure in the contract, kept deliberately literal:

  1. Ask for page 1 with the stored cursor. Keep that response's `server_time` as a candidate.
  2. Follow `next` until it is null.
  3. Upsert styles by `style_id`, variants by `blank_sku`.
  4. Save the candidate cursor only once every page has succeeded.
  5. A page boundary can repeat a style but never skip one, so repeats must be harmless.

Rows are never deleted. Ops sends archived styles and variants with `is_active: false`, and a
blank that a store is currently selling has to stay where it is until that store closes.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalog.models import Blank, BlankImage, BlankVariant

from .models import CatalogSyncState
from .ops_client import OpsClient

logger = logging.getLogger(__name__)

PAGE_SIZE = 100  # the contract's maximum; larger values are capped by ops


@dataclass
class SyncResult:
    styles: int = 0
    created: int = 0
    updated: int = 0
    variants: int = 0
    deactivated: list = field(default_factory=list)
    full: bool = False
    cursor: str = ""

    @property
    def summary(self):
        return (
            f"styles={self.styles} created={self.created} updated={self.updated} "
            f"variants={self.variants} deactivated={len(self.deactivated)} "
            f"mode={'full' if self.full else 'incremental'}"
        )


def _money(value):
    """A {"amount": "25.92", "currency": "USD"} block as (Decimal, currency), or (None, "USD")."""
    if not isinstance(value, dict):
        return None, "USD"
    amount = value.get("amount")
    currency = value.get("currency") or "USD"
    if amount in (None, ""):
        return None, currency
    try:
        return Decimal(str(amount)), currency
    except (InvalidOperation, TypeError):
        # Money is a string decimal by contract; anything else is ops' bug, not a reason to
        # abandon the whole sync.
        logger.warning("ops sent an unparseable amount %r", amount)
        return None, currency


def _text(value):
    """Nullable contract strings become blank, so templates never print the word None."""
    return "" if value is None else str(value)


@transaction.atomic
def apply_style(payload, now=None):
    """Upsert one style resource and its variants and images. Returns (blank, created, deactivated).

    `deactivated` lists the things this call saw go inactive, for the staff exceptions list.
    """
    now = now or timezone.now()
    amount, currency = _money(payload.get("base_cost"))
    blank, created = Blank.objects.get_or_create(
        style_id=payload["style_id"],
        defaults={"supplier_style_code": payload.get("supplier_style_code") or "", "ops_updated_at": now},
    )

    went_inactive = []
    if blank.apply_active(bool(payload.get("is_active", True)), now) and not blank.is_active:
        went_inactive.append(blank)

    blank.supplier_style_code = _text(payload.get("supplier_style_code"))
    blank.brand = _text(payload.get("brand"))
    blank.display_title = _text(payload.get("display_title"))
    blank.merch_label = _text(payload.get("merch_label"))
    blank.category = _text(payload.get("category"))
    blank.audience = _text(payload.get("audience"))
    blank.stock_policy = _text(payload.get("stock_policy"))
    blank.base_cost, blank.currency = amount, currency
    blank.size_chart = payload.get("size_chart")
    blank.ops_updated_at = parse_datetime(payload["updated_at"]) if payload.get("updated_at") else now
    blank.save()

    for row in payload.get("variants") or []:
        adjustment, variant_currency = _money(row.get("cost_adjustment"))
        variant, _ = BlankVariant.objects.get_or_create(
            blank_sku=row["blank_sku"], defaults={"blank": blank, "color_name": row.get("color_name") or ""},
        )
        if variant.apply_active(bool(row.get("is_active", True)), now) and not variant.is_active:
            went_inactive.append(variant)
        variant.blank = blank
        variant.color_name = _text(row.get("color_name"))
        variant.color_hex = _text(row.get("color_hex"))
        variant.size = _text(row.get("size"))
        variant.size_sort_order = row.get("size_sort_order")
        variant.cost_adjustment, variant.currency = adjustment, variant_currency
        variant.stock_policy = _text(row.get("stock_policy"))
        variant.save()

    # Images are best-effort and have no stable identity of their own, so they're replaced
    # wholesale rather than matched up.
    images = payload.get("images") or []
    blank.images.all().delete()
    BlankImage.objects.bulk_create([
        BlankImage(
            blank=blank,
            color_name=_text(row.get("color_name")),
            image_type=_text(row.get("image_type")),
            view=_text(row.get("view")),
            url=row.get("url") or "",
        )
        for row in images if row.get("url")
    ])
    return blank, created, went_inactive


def sync_catalog(client=None, full=False, state=None):
    """Run one sync. Raises OpsError if a page fails, leaving the stored cursor untouched."""
    client = client or OpsClient()
    state = state or CatalogSyncState.load()
    now = timezone.now()

    state.last_started_at = now
    state.save()

    updated_since = "" if full else state.cursor
    result = SyncResult(full=not updated_since)
    candidate = ""

    for index, page in enumerate(client.styles(updated_since=updated_since or None, page_size=PAGE_SIZE)):
        if index == 0:
            # Step 1: the candidate cursor comes from the first page, before anything is read,
            # so a style changed mid-run is picked up next time rather than missed.
            candidate = page.get("server_time") or ""
        for payload in page.get("results") or []:
            _, created, went_inactive = apply_style(payload, now=now)
            result.styles += 1
            result.created += created
            result.updated += not created
            result.variants += len(payload.get("variants") or [])
            result.deactivated.extend(went_inactive)

    # Step 4: only now is the cursor allowed to move.
    state.cursor = candidate or state.cursor
    state.last_finished_at = timezone.now()
    state.last_error = ""
    state.styles_seen = result.styles
    if result.full:
        state.last_full_sync_at = state.last_finished_at
    state.save()

    result.cursor = state.cursor
    return result
