# Ops ↔ Storefront API Contract

**Audience:** Claude Code sessions (and developers) working in either repository.
**Where this file lives:** Commit a copy to `docs/ops-storefront-api.md` in **both** repos (MyDjango and storefront). Reference it from each repo's `CLAUDE.md` with one line: `Integration contract with the other system: see docs/ops-storefront-api.md. Do not change the contract without updating this file in both repos.`

**Status:** Draft v1.2. Items marked **CONFIRM** must be checked against the actual code before implementing. Items marked **DECIDE** need the owner's call. Items marked **PENDING** are decided but not yet done.

**Changes in v1.2** (API A built on ops and verified, 2026-09-22)
- **API A is implemented on ops** (`channel_intake` app) and verified field by field against the ops database for three styles (multi-supplier, youth, archived). See §4.
- **The storefront is method-blind.** It is never told how a blank is printed (DTG, DTF, ROQ NOW, screen, etc.). `decoration_methods`, `requires_pretreat` and `GET /api/v1/catalog/decoration-methods/` are removed. Re-adding any of them later is an additive, non-breaking change (§3).
- `images[].s3_key` is replaced by `images[].url` (supplier CDN). `view` is always `null` in v1; `image_type` is added. Images are best-effort and may be empty.
- `supplier_style_code` is ops' `style_name`, the canonical style code across ops.
- `category` is ops' category name as free text. No slug.
- `base_cost` / `cost_adjustment` are defined: the preferred supplier's current price, sales included.
- New fields: `stock_policy` (style and variant), `size_sort_order` (variant). List responses carry `server_time`.
- Pagination: `page_size` (default 25, max 100), results ordered by `style_id`.
- Timestamps are to the second.
- Ops auth detail: tokens are stored hashed, with scopes.
- Several CONFIRMs resolved (§11). New **DECIDE**: API B `decoration[].method` conflicts with the method-blind storefront (§5, §11).

**Changes in v1.1**
- Order intake (API B) is now **channel-agnostic**: it is the single way any external sales channel sends orders to ops, not a school-store feature. `source` is an enum; `client_store` is replaced by a general `channel` block; new `account`, `packing`, and `external_refs` blocks; line items gain `line_type` to support stocked (pick-pack) goods alongside decorated goods.
- Status events (API C) carry `source` and per-line external references so the sending channel can route updates (e.g. write tracking back to a merchant's Shopify store).
- New §7 describes the channel model, including the future Shopify merchant app. **Only the `clientstore` channel is built now**; the contract is shaped so later channels need no v2.

---

## 0. How to work on this (instructions for Claude Code)

- **Read before writing.** Before any change in MyDjango, locate and read: the order models (`operations.Orders`, order items via `order.orderitems`), the store model and `store_nickname`, `COMPANY_STORES`, `is_3pl_order()`, `explode_order_to_productionitems()`, the SKU rules in the production explosion code (DTG/DTF/pick-pack prefixes, `-PT` pre-treat suffix), and the catalog, supplier and PIM tables (`blank_styles`, `blank_style_variations`, `supplier_product_part`, `variants`, `variant_metadata`, `pim_product_definitions`). Summarize what you found and flag anything that contradicts this memo **before** proposing code.
- **Existing behavior is sacred.** MyDjango runs production for live Shopify stores and existing 3PL clients. New code is additive: new app, new URLs, new models. Do not modify existing order ingestion, SKU parsing, or production explosion logic except where a phase below says so, and then show the diff and wait for approval.
- **Schema changes need sign-off.** Ops (MyDjango) never uses Django migrations: every table is created by hand-written SQL that the owner runs, and every model is `managed = False`. On the storefront, propose migrations and explain them; do not run `migrate` against any shared or production database.
- **One phase at a time.** Each phase ends with verification steps. Stop after each phase, report results, and wait.
- **Stack constraints:** Python/Django, Django REST Framework, MariaDB, cron for scheduled work (**no Celery, no Redis, no queues**). Ops MariaDB is 10.5 (no `SKIP LOCKED`). Ops currently runs Django `runserver` behind the shared ALB; saving a file reloads the live site, so new ops code is added in an order that never lets it import an unfinished file. Don't introduce new infrastructure.
- **Build only the `clientstore` channel.** Where this memo describes `shopify_app` or other channels, implement only what's needed for the contract to accept them later (enum values, nullable fields, validation branches that return a clear "channel not enabled" error). Don't build Shopify app code.
- **Ask, don't guess,** on anything marked CONFIRM or DECIDE.

## 1. The two systems and the boundary

| | **Ops (MyDjango)** | **Storefront platform** |
|---|---|---|
| Owns | PIM/blanks, SKUs, orders, production items, warehouse & 3PL inventory, shipping, tracking, **how every item is produced** | Sales channels: client stores (now), Shopify merchant app (later). Catalogs & pricing per channel, carts, payments/billing, buyer & merchant notifications, store lifecycle, settlement |
| Database | Existing MariaDB | Separate database. **Never** connects to ops DB. |
| Host | Existing EC2 (`app.fulfillem.com`) | New EC2, separate domain, same ALB via host routing |

Rules:
1. The only ways data crosses the boundary are the three APIs below and the shared S3 artwork bucket.
2. **Ops mints all SKUs and style IDs.** The storefront never invents a SKU; it stores ops identifiers.
3. **Artwork lives in one S3 bucket**, referenced by key from both sides. No file copying.
4. **Money stays in the storefront.** Ops only ever receives orders that are paid or billable, and never sees card data.
5. **Ops is channel-blind below the intake.** After API B accepts an order, production, pack-out, and shipping treat every source the same. Channel-specific behavior (e.g. writing tracking to Shopify) lives in the storefront.
6. **The storefront is method-blind.** It sells blanks and designs; it is never told, and never decides, how an item is printed.
7. Either system must tolerate the other being down: the storefront keeps selling on its cached catalog; orders queue in the storefront outbox; status updates queue in the ops outbox.

## 2. The three APIs at a glance

| # | Direction | Endpoint(s) | Pattern | Build phase |
|---|---|---|---|---|
| A | Ops → Storefront | `GET /api/v1/catalog/styles/...` on ops | Storefront **pulls** (cron + on demand), caches locally | Phase 1: **ops side built and verified**; storefront side next |
| B | Storefront → Ops | `POST /api/v1/orders/` on ops | **Universal order intake** for all channels; storefront pushes via outbox, idempotent | Phase 2 (build now, enable after pilot) |
| C | Ops → Storefront | `POST /api/v1/ops-events/` on storefront | Ops **pushes** webhooks via outbox, signed | Phase 3 |

**Pilot note:** the first friendly-client store(s) may be hand-keyed into ops even after API B exists, so the order payload can be validated against reality. API B sending is behind a feature flag (`OPS_ORDER_SYNC_ENABLED`, per store/channel account) and stays off until the owner turns it on.

## 3. Shared conventions (both directions)

- **Base path & versioning:** `/api/v1/`. Breaking changes → `/api/v2/` alongside v1; never change v1 semantics in place. Additive optional fields are non-breaking. New `source` enum values are additive **only if** the receiver rejects unknown values cleanly (`422 channel_not_enabled`), which v1 must do.
- **Format:** JSON, UTF-8, `snake_case`. Timestamps ISO 8601 UTC with `Z`, to the second (`2026-09-22T14:03:00Z`). Money as **string decimals** (`"24.00"`) with a `currency` field (`"USD"`), never floats.
- **Auth (system-to-system):** DRF token format, header `Authorization: Token <key>`, using a dedicated service user per calling system (`svc_storefront` on ops; `svc_ops` on storefront). Tokens in SSM Parameter Store / env, never in the repo. Service users have only the permissions these endpoints need. All channels share the storefront's service user; the channel is identified in the payload, not by separate credentials.
  - **Ops implementation:** tokens are stored only as SHA-256 hashes in `channel_api_tokens`, each with scopes. API A requires `catalog:read`; API B will add `orders:write`. Tokens are issued, listed and revoked with `manage.py channel_api_token`. Several live tokens per service user are allowed so a token can be rotated without downtime: create the new one, switch the caller, revoke the old.
- **Network:** both instances sit behind the same ALB. Ops `/api/v1/*` is to be restricted by an ALB listener rule: path `/api/v1/*` from the storefront's egress IP → forward; any other `/api/v1/*` → fixed 403. **PENDING** (separate memo). Until then the ops endpoints are reachable from the internet and protected by token only.
- **Webhook signing (API C, and optionally B):** header `X-Signature: sha256=<hex HMAC of raw body>` using a shared secret, plus `X-Timestamp`; reject if older than 5 minutes. Constant-time compare.
- **Idempotency:** every write carries an `Idempotency-Key` header (UUID generated once by the sender and persisted with the outbox row). Receiver stores keys with the resulting object; a repeat returns the original response with `200` instead of `201`. Additionally, `(source, external_order_id)` is unique on the receiver.
- **Errors:** body `{"error": {"code": "...", "message": "...", "details": {...}}}`.
  - `400` validation (do not retry; exceptions queue)
  - `401/403` auth (do not retry; alert)
  - `404` unknown reference, e.g. unknown SKU (do not retry; exceptions queue)
  - `409` conflict / state violation (do not retry; exceptions queue)
  - `422` recognized but not enabled, e.g. `channel_not_enabled` (do not retry; alert)
  - `429`, `5xx`, timeouts (retry with backoff)
- **Timeouts:** client connect 5s, read 30s.
- **Logging:** log request ID, idempotency key, source, status, and duration for every call on both sides. Never log tokens or full personal data. (Ops logs the ALB's `X-Amzn-Trace-Id` as the request ID.)

## 4. API A — Catalog (ops → storefront, read-only)

**Purpose:** give the storefront everything it needs to offer blanks in any channel without direct DB access.

### Endpoints (on ops)

```
GET /api/v1/catalog/styles/?updated_since=<ts>&page=<n>&page_size=<n>
GET /api/v1/catalog/styles/<style_id>/
```

Both require a token with the `catalog:read` scope.

| Parameter | Meaning |
|---|---|
| `updated_since` | Optional. ISO 8601 with an explicit offset; send `Z`. Returns styles whose `updated_at` is **at or after** this time. Send the previous sync's `server_time` unchanged. A `+00:00` offset must be URL-encoded; `Z` avoids that. |
| `page` | 1-based. A page past the end returns `404 not_found`. |
| `page_size` | Default 25, maximum 100 (larger values are capped). |

Archived styles and archived variants **are included**, with `is_active: false`, so the storefront can mark them inactive locally.

### List response

```json
{
  "count": 214,
  "next": "https://app.fulfillem.com/api/v1/catalog/styles/?page=2",
  "previous": null,
  "server_time": "2026-09-22T22:32:33Z",
  "results": [ "<style resource>", "..." ]
}
```

Results are ordered by `style_id`. The detail endpoint returns one style resource with no envelope.

### Style resource (shape)

```json
{
  "style_id": 407,
  "supplier_style_code": "CC1567",
  "brand": "Comfort Colors",
  "display_title": "COMFORT COLORS Adult Ringspun Hooded Sweatshirt",
  "merch_label": null,
  "category": null,
  "audience": "adult",
  "is_active": true,
  "stock_policy": "job_only",
  "base_cost": {"amount": "25.92", "currency": "USD"},
  "size_chart": null,
  "variants": [
    {
      "color_name": "BlueJean",
      "color_hex": "#596B83",
      "size": "S",
      "size_sort_order": 2,
      "blank_sku": "CC1567-BlueJean-S",
      "cost_adjustment": {"amount": "0.00", "currency": "USD"},
      "is_active": false,
      "stock_policy": "job_only"
    }
  ],
  "images": [
    {
      "color_name": "BlueJean",
      "view": null,
      "image_type": "large",
      "url": "https://www.carolinamade.com/prodimg/large/cc1567BJN_092024112321.png"
    }
  ],
  "updated_at": "2026-09-22T20:37:09Z"
}
```

### Field reference

| Field | Type | Meaning and ops source |
|---|---|---|
| `style_id` | int | Ops style ID. Stable, minted by ops. The storefront's key for a blank. |
| `supplier_style_code` | string | Ops `blank_styles.style_name` (e.g. `CC1567`, `G18600`), the canonical code every ops mapping table uses. Unique. Despite the name, this is **not** any one supplier's internal ID. |
| `brand` | string \| null | Brand name. |
| `display_title` | string \| null | Supplier catalog copy. **Staff-facing only; never show it to customers.** |
| `merch_label` | string \| null | Customer-facing garment name, owned by ops marketing (e.g. "Terry Hoodie"). Null on most styles today. Ops' own publishing (PIM) refuses to publish a product without one; the storefront decides its own fallback. |
| `category` | string \| null | Ops category name, free text (e.g. `Hoodies`, `Youth Crewneck`). Ops staff can rename categories; a rename changes `updated_at`. |
| `audience` | `adult` \| `youth` \| `toddler` \| null | From the sizes of the style's active variants (all variants if none are active). Null when sizes span more than one audience. One Size and Adjustable count as adult. |
| `is_active` | bool | False when ops has archived the style. |
| `stock_policy` | `stocked` \| `seasonal` \| `sell_down` \| `job_only` \| null | Ops' stocking classification; null = unclassified. Informational: e.g. the storefront may choose not to offer `sell_down` blanks in new stores. |
| `base_cost` | money \| null | Lowest variant cost among active variants (all variants if none are active). Null when no variant has a price. See **Cost** below. |
| `size_chart` | null | Always null in v1. |
| `variants[]` | list | Every variant, active and archived. |
| `variants[].blank_sku` | string | Ops-minted, unique. The storefront's key for a variant. |
| `variants[].color_name` | string | Ops color token (e.g. `BlueJean`), the same value ops publishes to Shopify as the color option. |
| `variants[].color_hex` | string \| null | `#RRGGBB`, uppercase. |
| `variants[].size` | string \| null | Size abbreviation (`S`, `2XL`, `YM`, `3T`, `OS`). |
| `variants[].size_sort_order` | int \| null | Sort ascending for display. Not contiguous. |
| `variants[].cost_adjustment` | money \| null | This variant's cost minus `base_cost`. Can be negative on an archived variant. Null when the variant has no price. |
| `variants[].is_active` | bool | False when ops has archived the variant (or its style). |
| `variants[].stock_policy` | same values as style | Effective policy: the color's override if set, else the style's. |
| `images[]` | list | **Best-effort; may be empty.** Per color. |
| `images[].color_name` | string | Matches `variants[].color_name`. |
| `images[].view` | null | Always null in v1: supplier data does not say which side of the garment an image shows. Reserved for `front` / `back`. |
| `images[].image_type` | string | The supplier's own label: `large`, `zoom`, `alternate`, `swatch`, `thumbnail`, `primary`. |
| `images[].url` | string | Supplier CDN URL (https), hotlinked. It can change or disappear; the storefront should cache or copy what it displays. |
| `updated_at` | timestamp | See **updated_at** below. |

**Cost.** A variant's cost is the current qty-1 unit price (`supplier_product_part.unit_price`, "what ops pays per unit today") from the most-preferred supplier linked to the style that carries the variant and has a price. Preference is ops purchasing's buyer preference (currently Carolina Made, then S&S, then SanMar, then Supplyable), with the style's supplier link rank as a tie-breaker. Supplier stock is ignored. **Sales are included:** a cost can drop during a supplier promotion and rise when it ends. Pricing tiers are the storefront's concern and are not in this API.

**updated_at.** The latest change to anything the style's payload is built from: the style, its variations, its brand and category, the color table, linked supplier prices (`prices_synced_at`), supplier links, supplier images, and ops' supplier preference settings. Because price confirmations bump it, many styles change daily. Some changes do **not** move it (for example, a supplier part being deactivated), so the storefront also runs a periodic full sync.

### Errors (API A)

| Status | `error.code` | When |
|---|---|---|
| 401 | `not_authenticated` | No `Authorization` header (response also carries `WWW-Authenticate: Token`) |
| 401 | `authentication_failed` | Unknown, revoked or expired token, or a malformed header |
| 403 | `permission_denied` | Valid token without `catalog:read` |
| 400 | `invalid` | Bad `updated_since`; `error.details.updated_since` explains |
| 404 | `not_found` | Unknown `style_id`, or a page past the end |
| 500 | `server_error` | Unexpected; retry with backoff |

### Out of scope for v1 and reserved paths
- **Live supplier stock.** Reserve `POST /api/v1/catalog/availability/` (list of `blank_sku` + quantity) for the store-close availability check; don't build it yet.
- **Reserved for later (not v1):** `GET /api/v1/inventory/?account_ref=...` exposing a 3PL client's stocked on-hand quantities, needed when merchants sell stocked goods through a channel.
- **Removed in v1.2:** `GET /api/v1/catalog/decoration-methods/`, `decoration_methods`, `requires_pretreat` (the storefront is method-blind).

### Storefront side
- Management command `sync_catalog` run by cron nightly, plus a "Refresh now" button in the staff console.
- **Sync procedure:**
  1. Request page 1 with `updated_since` = the stored cursor (omit it for a full sync). Store that response's `server_time` as the candidate cursor.
  2. Follow `next` until it is null.
  3. Upsert by `style_id`, and variants by `blank_sku`.
  4. Only after every page succeeds, save the candidate cursor. On any failure, keep the old cursor; the next run repeats the work, which is safe.
  5. A page boundary can repeat a style but never skip one. Upserts make repeats harmless.
- **Full sync** (no `updated_since`) at least weekly, and whenever the cursor is lost.
- Expect about 1.5–2 seconds per page of 25; the full catalog is roughly 9 pages.
- Local copy in `catalog.Blank` / `catalog.BlankVariant`. Never deletes locally; marks inactive. **A blank used by an open store is never removed from that store mid-sale**; inactivation surfaces in the staff exceptions queue.

### Phase 1 verification
- **Ops: done (2026-09-22).**
  - Payloads for styles 261 (multi-supplier, where link order and buyer preference disagree), 35 (youth) and 234 (archived) were compared field by field against independent SQL: 0 mismatches.
  - Over HTTPS through the ALB: no token → 401 with `WWW-Authenticate: Token`; wrong token → 401; storefront token → 200 with `count` 214; unknown style → 404; bad `updated_since` → 400; page past end → 404; `page_size=500` capped at 100; `updated_since=<server_time>` a moment later → 0 results.
- **Storefront: pending.** `sync_catalog` run twice in a row produces no changes the second time.

## 5. API B — Universal order intake (storefront → ops)

**Purpose:** the single path by which orders from **any** external sales channel enter ops, landing in the existing pipeline exactly like other 3PL orders.

### Endpoints (on ops)

```
POST /api/v1/orders/                       create one order (idempotent)
GET  /api/v1/orders/<ops_order_id>/        read back status
POST /api/v1/orders/<ops_order_id>/cancel/ request cancellation (409 if already in production)
```

### Sources (channels)

| `source` | Meaning | v1 status |
|---|---|---|
| `clientstore` | Group/private-label store on the storefront platform (schools, departments, small businesses) | **Enabled** |
| `shopify_app` | Order from a merchant's own Shopify store via our Shopify app (see §7) | Accepted by schema, returns `422 channel_not_enabled` until enabled |
| `manual` | Staff-entered via storefront console (e.g. invoiced bulk orders) | Accepted by schema, `422` until enabled |

Ops keeps an allow-list setting `ORDER_INTAKE_ENABLED_SOURCES = ["clientstore"]`.

### When the storefront sends (clientstore)
- **Individual-ship stores:** one ops order per buyer order, at payment or at store close. **DECIDE** default.
- **Group-ship stores:** at store close, either one ops order per store batch or one per buyer sharing a `batch_id`. **DECIDE.** The payload supports both.

### Request (shape)

```json
{
  "external_order_id": "SF-2026-000123",
  "source": "clientstore",

  "channel": {
    "channel_ref": "store:88",
    "name": "Lincoln High Spirit Wear – Fall 26",
    "url": "https://lincoln-high.yourstores.example"
  },

  "account": {
    "account_ref": "org:41",
    "name": "Lincoln High PTA",
    "billing_mode": "prepaid"
  },

  "batch_id": "SF-BATCH-88-01",
  "fulfillment_mode": "individual_ship",
  "placed_at": "2026-09-22T15:10:00Z",
  "promised_ship_by": "2026-10-10",

  "buyer": {"name": "Jane Parent", "email": "jane@example.com", "phone": null},
  "ship_to": {
    "name": "Jane Parent", "company": null,
    "address1": "1 Main St", "address2": null,
    "city": "Selma", "province_code": "AL", "postal_code": "36701", "country_code": "US"
  },
  "shipping_method": "ground",

  "packing": {
    "packing_slip_brand": "channel",
    "brand_name": "Lincoln High PTA",
    "logo_s3_key": "branding/org-41/logo.png",
    "return_address": null,
    "gift_message": null,
    "insert_skus": [],
    "blind_ship": false
  },

  "external_refs": {
    "shopify_order_id": null,
    "shopify_fulfillment_order_id": null
  },

  "line_items": [
    {
      "external_line_id": "SF-LI-5551",
      "line_type": "decorated",
      "sku": "PODICA-....",
      "blank_sku": "BC3001CVC-HNVY-M",
      "style_id": 1234,
      "quantity": 2,
      "decoration": [
        {"method": "dtg", "location": "front", "design_file_key": "artwork/88/front-v3.png", "width_in": "11.0"}
      ],
      "personalization": {"name": "SMITH", "number": "12"},
      "recipient_label": "Ava – 5th grade",
      "unit_price": {"amount": "24.00", "currency": "USD"},
      "external_refs": {"shopify_line_item_id": null, "shopify_fulfillment_order_line_item_id": null}
    },
    {
      "external_line_id": "SF-LI-5552",
      "line_type": "stocked",
      "sku": "LGTB-MUG-11OZ",
      "inventory_account_ref": "org:41",
      "quantity": 1,
      "decoration": [],
      "personalization": null,
      "recipient_label": null,
      "unit_price": {"amount": "14.00", "currency": "USD"},
      "external_refs": {"shopify_line_item_id": null, "shopify_fulfillment_order_line_item_id": null}
    }
  ],
  "notes": null
}
```

**DECIDE (v1.2):** `decoration[].method` asks the storefront to name a print method, which conflicts with the method-blind rule (§1 rule 6). Resolve before Phase 2 is built: for example, drop `method` and let ops route from the blank and the design, or keep a method-neutral placement description (`location`, `width_in`) only. Note that ops' ROQ NOW recipe selection currently relies on a `-PT` / `-NOPT` pre-treat signal on the decorated SKU; ops must still derive that signal itself.

### Field rules
- **`source`**: enum per the table above. Unknown value → `400`; known but not enabled → `422 channel_not_enabled`.
- **`channel`**: where the sale happened. `channel_ref` is the storefront's stable identifier, prefixed by type (`store:88`, later `shop:acme-apparel.myshopify.com`). Used for reporting and packing, not for routing in ops.
- **`account`**: the storefront-side customer who is billed or settled (a PTA, a business, later a Shopify merchant). `billing_mode`: `prepaid` (buyer paid by card, v1) | `merchant_billed` (merchant charged cost of goods, later) | `invoiced` (PO/net terms, later). Ops uses `account_ref` to find or create the ops store/client record (see ops behavior).
- **`fulfillment_mode`**: `individual_ship` | `group_ship`. For `group_ship`, `ship_to` is the organization's delivery address and every line needs `recipient_label`.
- **`packing`**: what goes on and in the box.
  - `packing_slip_brand`: `platform` (our brand) | `channel` (the account's brand, using `brand_name` + `logo_s3_key`) | `none`.
  - `blind_ship: true` means no reference to us anywhere on the shipment (required for merchant channels). `return_address` overrides ours when set.
  - `insert_skus`: stocked inserts (thank-you cards, stickers) owned by the account, picked from 3PL inventory. v1 may accept and ignore with a warning if ops has no insert handling. **CONFIRM** current packing slip generation and whether per-order branding is supported; if not, v1 stores the block and prints platform-branded slips, reported as a gap.
- **`external_refs`** (order and line level): opaque identifiers from the originating channel. Ops stores and echoes them back in API C events but never interprets them. All nullable; always null for `clientstore`.
- **`line_type`**:
  - `decorated`: produced by ops (DTG/DTF/etc.). Requires `sku` plus `decoration` with at least one entry, or a `sku` that ops can already explode on its own.
  - `stocked`: picked from 3PL inventory owned by `inventory_account_ref`. Requires `sku` of an existing inventory item for that account. No decoration.
  - Mixed orders (decorated + stocked) ship together; ops must hold stocked lines until decorated lines are produced. **CONFIRM** whether pick-pack items in a mixed order already wait for production in the current pipeline.
- **`sku`** must be ops-minted. **CONFIRM** the minting approach for decorated channel products: (a) storefront requests a SKU from ops when a product is published (`POST /api/v1/catalog/skus/`), or (b) ops accepts `blank_sku` + `decoration` and derives production routing without a per-design SKU. Read `explode_order_to_productionitems` / `_explode_shopify_line_item` and recommend one; do not implement until approved. The choice must work for both `clientstore` and future `shopify_app` products, and must respect the method-blind rule.
- **`unit_price`** is informational for ops (packing slips, customs, insurance). Payment happens in the storefront.
- Personal data minimization: children's names appear only in `recipient_label` / `personalization`, only as needed to print and sort.

### Ops-side behavior
- New Django app `channel_intake` (exists since Phase 1) containing serializers, views, and:
  - `ExternalOrderRef`: `source`, `external_order_id`, `idempotency_key`, `ops_order` FK, `payload` JSON, `created_at`; unique on `(source, external_order_id)`.
  - `ExternalAccountMap`: `source`, `account_ref`, ops store/client FK. Maps a storefront account to the ops store record used for 3PL identification, billing reports, and stocked-inventory ownership.
- Creates records using the **existing** order models so everything downstream works unchanged.
- **3PL identification. CONFIRM** that `is_3pl_order()` returns `True` for these orders without code changes. Preferred: each `account_ref` maps to an ops store record whose nickname is not in `COMPANY_STORES`. **DECIDE:** one ops store record per account (per PTA, per merchant: better per-client reporting and inventory ownership) vs. one per source (simpler). Recommendation: per account. Do not edit `is_3pl_order` unless required; if required, show the diff.
- Validation (return `400/404`, never create partial orders): unknown SKU; inactive blank; decorated line missing a design file in S3; group-ship line without `recipient_label`; stocked line whose SKU isn't owned by `inventory_account_ref`; `blind_ship: true` with `packing_slip_brand: platform`.
- Whole order in one DB transaction.
- Response `201`:
```json
{"ops_order_id": 98765, "ops_order_number": "CS-98765", "status": "received", "source": "clientstore", "external_order_id": "SF-2026-000123"}
```
- **CONFIRM** an order-number prefix per source (`CS-` clientstore, `SA-` shopify_app) so the floor can tell channels apart at a glance, if existing numbering allows.

### Storefront side (outbox)
- `integrations.OutboxMessage`: `id`, `kind` (`order.create`, `order.cancel`), `source`, `payload` JSON, `idempotency_key` (UUID set at creation), `status` (`pending`/`sent`/`failed`/`dead`), `attempts`, `next_attempt_at`, `last_error`, timestamps.
- The order state change and the outbox row are written **in the same transaction**.
- Payload building is per-channel (`channels/<source>/ops_payload.py`), delivery is shared. Only `channels/clientstore/` exists in v1.
- Cron runs `manage.py deliver_outbox` every minute with a lock (`flock`, or `SELECT ... FOR UPDATE SKIP LOCKED` on MariaDB 10.6+; **CONFIRM** the storefront's MariaDB version). Backoff: 1m, 5m, 15m, 1h, 6h; after 8 attempts → `dead`, shown in the staff exceptions queue with an alert.
- On `201/200` store `ops_order_id` on the storefront order.
- Feature flag: nothing is enqueued unless `OPS_ORDER_SYNC_ENABLED` is on for the store/account.

### Phase 2 verification
- Same payload twice with the same `Idempotency-Key` → one ops order; second response `200` with the same body.
- Same `(source, external_order_id)` with a different key → `409`.
- Created test order explodes into `ProductionItem`s via the existing function with the expected production type and pre-treat flag, for one DTG-with-pretreat, one DTF, and one stocked/pick-pack line in a single mixed order.
- `source: "shopify_app"` → `422 channel_not_enabled`, no rows created.
- Invalid SKU → `404`, no rows created.
- `packing` and `external_refs` are persisted and retrievable via `GET /api/v1/orders/<id>/`.
- Kill the ops API mid-run: the outbox retries and delivers once it's back; no duplicates.
- All tests against a local/dev DB only.

## 6. API C — Status events (ops → storefront, webhooks)

**Purpose:** drive buyer/merchant notifications, the client dashboard, settlement, and (later) writing fulfillment back to external channels.

### Endpoint (on storefront)

```
POST /api/v1/ops-events/
```

### Event (shape)

```json
{
  "event_id": "evt_01J...",
  "event_type": "order.shipped",
  "occurred_at": "2026-10-08T19:22:00Z",
  "source": "clientstore",
  "ops_order_id": 98765,
  "external_order_id": "SF-2026-000123",
  "external_refs": {"shopify_order_id": null, "shopify_fulfillment_order_id": null},
  "data": {
    "shipments": [
      {
        "carrier": "UPS", "service": "Ground",
        "tracking_number": "1Z...", "tracking_url": "https://...",
        "lines": [
          {"external_line_id": "SF-LI-5551", "quantity": 2,
           "external_refs": {"shopify_fulfillment_order_line_item_id": null}}
        ]
      }
    ]
  }
}
```

Event types (v1): `order.received`, `order.in_production`, `order.shipped`, `order.partially_shipped`, `order.cancelled`, `line.exception` (`data.reason`: `blank_unavailable` | `misprint` | `stock_short` | `artwork_problem` | `other`; `data.lines`).

Rules:
- Ops echoes back `source` and all `external_refs` exactly as received in API B.
- Shipments report quantities per line, so partial shipments and split boxes map cleanly to channel fulfillments.
- Signed with HMAC per §3; the storefront rejects unsigned or stale requests.
- `event_id` is the idempotency key; the storefront stores processed IDs and ignores repeats.
- Events may arrive out of order; the storefront applies them only if they move the order forward (`occurred_at` + state precedence).
- Storefront dispatches each event to `channels/<source>/on_ops_event.py` (v1: send buyer emails, update client dashboard). Later, `shopify_app` will create Shopify fulfillments from the same event.
- Ops side: `OpsOutboxMessage` model + `deliver_ops_events` cron command, with the same retry/backoff as §5. Emit events from the places ops already changes order status and records shipments, only for orders that have an `ExternalOrderRef`. **CONFIRM** those hook points; prefer explicit calls at the few places status changes, and list them before editing.

### Phase 3 verification
- Mark a test order shipped in ops → the storefront receives `order.shipped` with per-line quantities, stores tracking, and sends a buyer email (dev: console email backend).
- Replay the same event → no duplicate email.
- Bad signature → `401`, nothing processed.
- Orders without an `ExternalOrderRef` (existing Shopify/3PL orders) emit no events.

## 7. Channel model (context for future work — do not build beyond v1)

The storefront platform is the home for every external sales channel. Each channel is an adapter that turns its sales into API B payloads and turns API C events back into channel actions. Ops never needs to know which channel it is beyond `source`.

| Channel | Who buys | Who is billed | Packing | Status |
|---|---|---|---|---|
| `clientstore` | Parents, members, employees on our hosted store | Buyer pays by card at checkout (`prepaid`) | Channel-branded or platform-branded slip | **v1** |
| `shopify_app` | Customers of a merchant's own Shopify store | Merchant charged cost of goods to a card/wallet on file (`merchant_billed`) | `blind_ship: true`, merchant-branded slip, optional inserts | Later |
| `manual` | Fire department bulk/PO orders entered by staff | `invoiced` | Channel-branded | Later |
| `etsy`, `woocommerce` | Same pattern as `shopify_app` | `merchant_billed` | Blind | Possible later |

**Shopify merchant app outline** (so present choices don't block it):
- Merchant installs our app (custom distribution for the first few merchants, public listing later), picks blanks, uploads art, approves mockups, and we create products in *their* store with the cost field populated, assigned to our fulfillment service location.
- Shopify sends fulfillment requests to the app; the app accepts, builds an API B payload with `source: "shopify_app"`, `channel_ref: "shop:<myshopify domain>"`, `account_ref: "merchant:<id>"`, `billing_mode: "merchant_billed"`, `blind_ship: true`, and Shopify IDs in `external_refs`.
- Cost of goods is charged to the merchant through our payment processor before sending to ops; app subscription fees go through Shopify billing.
- API C `order.shipped` → the app creates a Shopify fulfillment against the fulfillment order line items in `external_refs`.
- Stocked lines (`line_type: stocked`) let merchants mix goods they store in our 3PL with POD items in one shipment. This is the main differentiator versus pure-POD providers, and why `line_type` and `inventory_account_ref` exist in v1.

## 8. File layout

**MyDjango (ops)** — built so far marked ✅
```
channel_intake/
  apps.py                ✅
  models.py              ✅ ApiServiceToken
                            later: ExternalOrderRef, ExternalAccountMap, OpsOutboxMessage
  authentication.py      ✅ HashedTokenAuthentication (Token header, hashed lookup)
  permissions.py         ✅ HasTokenScope (view.required_scope)
  api_base.py            ✅ ContractAPIView, contract error handler, CatalogPagination
  services/catalog.py    ✅ API A payloads and updated_at
  views/catalog.py       ✅ style list and detail
  urls.py                ✅ mounted at /api/v1/
  management/commands/
    channel_api_token.py ✅ create / list / revoke tokens
    deliver_ops_events.py   later
  services/              later: order_ingest.py, account_mapping.py, events.py
  settings keys:         later: ORDER_INTAKE_ENABLED_SOURCES
  tests/                 later
```
Ops tables are created by hand-written SQL (§0). Existing ops files touched so far: `mydjango/settings.py` (`INSTALLED_APPS`, `PUBLIC_URLS`) and `mydjango/urls.py` (the `/api/v1/` mount).

**Storefront**
```
apps/integrations/
  ops_client.py      thin HTTP client (requests), auth, timeouts, error mapping
  models.py          OutboxMessage, ProcessedOpsEvent
  webhooks.py        /api/v1/ops-events/ view, signature check, dispatch by source
  management/commands/sync_catalog.py
  management/commands/deliver_outbox.py
  tests/
apps/channels/
  base.py            ChannelAdapter interface: build_ops_payload(order), on_ops_event(event)
  clientstore/       ops_payload.py, on_ops_event.py   (v1)
  # shopify_app/     later; do not create in v1
```

Cron entries (both boxes) use the venv's `python manage.py <command>` wrapped in `flock -n /tmp/<command>.lock`.

## 9. Testing expectations
- Contract tests on each side using the JSON examples in this memo as fixtures. If a payload changes, the fixture and this memo change in the same commit.
- Ops intake tests include: one line per production type (DTG with pre-treat, DTF, pick-pack/stocked) in a single mixed order; a group-ship order; a `shopify_app` payload (expect `422`); a `blind_ship` payload (packing persisted).
- No test may call the other system's live endpoint; use a mocked client or `responses`.

## 10. Out of scope for v1
Building the Shopify merchant app or any non-`clientstore` channel adapter; merchant billing; live supplier availability (path reserved); 3PL inventory API (path reserved); invoiced/PO orders; returns/RMAs via API; partial line cancellation after production start; multiple ops warehouses; client custom domains; GraphQL; message queues; any print-method information in the catalog (removed in v1.2).

## 11. Open decisions

**Open**
- **DECIDE:** API B `decoration[].method` versus the method-blind storefront (§5).
- **DECIDE:** SKU minting approach for decorated channel products; must suit both `clientstore` and `shopify_app` (§5).
- **DECIDE:** individual-ship orders sent at payment vs. at store close (§5).
- **DECIDE:** group-ship as one batch order vs. per-buyer orders sharing `batch_id` (§5).
- **DECIDE:** one ops store record per account vs. per source (§5; recommendation: per account).
- **CONFIRM:** how ops identifies these as 3PL orders without changing `is_3pl_order` (§5).
- **CONFIRM:** current packing slip generation and support for per-order branding / blind ship (§5).
- **CONFIRM:** whether stocked lines in mixed orders already wait for decorated lines (§5).
- **CONFIRM:** per-source order-number prefixes (§5).
- **CONFIRM:** storefront MariaDB version, for `SKIP LOCKED` (§5).
- **PENDING:** ALB listener rule restricting ops `/api/v1/*` to the storefront's egress IP (§3).

**Known data gaps on ops (API A works; the data is incomplete)**
- Images: most active styles currently return few or no images, because supplier color codes on parts and on images disagree (e.g. youth styles, where image codes carry a trailing `Y`). The fix belongs in ops' supplier import, not the API.
- `merch_label` unset on most active styles; `category` unset on some.
- `size_chart` has no source.

**Resolved in v1.2**
- `merch_label` lives in `blank_styles.merch_label`; the base value is sent. Per-store overrides (`pim_style_store_labels`) are ops-only and not sent.
- `base_cost`, color hex and images: sources defined in §4.
- Ops MariaDB is 10.5.29 (no `SKIP LOCKED`); ops scheduled work uses `flock`.
- Network path: both instances share one ALB; the restriction is an ALB listener rule (PENDING above).
