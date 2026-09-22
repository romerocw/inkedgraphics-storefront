# Inked Graphics Storefront

Django 6 app for private-labeled group stores (schools, teams, businesses). Buyers shop a
time-limited store, pay via Stripe Checkout; staff manage clients/stores in a console.

## Layout
- `config/` settings, urls, health-check middleware (`/health/`)
- `stores/` Client, Store, StoreStatusChange, public store pages, base templates;
  `lifecycle.py` (the open/close schedule) + `manage.py lifecycle_tick`
- `config/heartbeat.py` cron jobs' heartbeat files in `RUN_DIR`
- `catalog/` Product, ProductVariant (size/color, `upcharge`), StoreProduct (per-store price)
- `orders/` Order/OrderItem (prices snapshotted), session Cart, checkout, Stripe (`payments.py`),
  buyer emails (`emails.py`), signed order links (`links.py`)
- `messaging/` the email outbox: `OutboxEmail`, `outbox.enqueue()`, `manage.py send_outbox`
- `console/` staff UI at `/console/` (login required, `is_staff`)
  - stores: list -> store page (`?tab=summary|products|orders`), edit at `stores/<pk>/edit/`
  - orders: `orders/` (filters, 50/page), `orders/<order_number>/` (with "Resend confirmation"
    for paid orders), CSV at `stores/<pk>/orders.csv`
  - catalog: `products/`, `products/new/`, `products/<pk>/` (product + variants in one form)
  - accounts: `profile/` (own details), `password/`, `team/` + `team/<pk>/` (owner/manager only)
  - `emails/`: outbox rows, status filter, retry failed (owner/manager only)
  - public pages: `login/`, `forgot/` (4 reset steps), `invite/<token>/`
  - `StaffProfile` (role, phone, job title, invited_by), `permissions.py`, `invitations.py`, `mail.py`
- `assets/input.css` Tailwind v4 source -> built to `stores/static/stores/site.css`
- `deploy/` `uwsgi.ini`, `deploy.sh`, `cron.d/storefront`, `logrotate.d/storefront`,
  `cloudwatch-agent.json`, `MONITORING.md` (CloudWatch setup and the lifecycle alarm)

## Conventions
- Settings read `../.env` (one level above manage.py); never commit `.env`.
- Local dev: `DB_ENGINE=sqlite`, `DEBUG=1`, local media. Prod: MariaDB, S3 media, Stripe keys.
- Cart holds one store at a time. Order totals come from `Order.recalculate()`.
- `Store.primary_color` overrides `Client.primary_color`; blank = inherit.
- **Time zones.** `TIME_ZONE` is `America/New_York` (was UTC until migration
  `stores/0005`, which re-read existing store times as Eastern wall-clock). Each store has a
  `time_zone`: its open/close times are typed in the store form and shown everywhere
  (console, buyer page, emails) in that zone, labelled, via `store.opens_local` /
  `store.closes_local`. Never render `store.opens_at`/`closes_at` directly — the template
  would convert them to Eastern. Everything else (orders, logs) shows in Eastern. Staff work
  across zones, so always label times.
- **Store lifecycle.** `lifecycle_tick` (cron, every 5 minutes) opens scheduled stores at
  `opens_at` and closes open ones at `closes_at` (straight to closed if both passed); drafts,
  closed stores and missing dates are never touched. Every status move — schedule or staff —
  is a `StoreStatusChange`; the console store form logs manual ones. A staff change at or
  after the scheduled moment wins (stores get reopened); a new date re-arms the schedule.
  Its summary line prints every run: CloudWatch alarms if it stops (`deploy/MONITORING.md`).
- **Arrival promise.** "When will it arrive?" has one answer, computed by
  `stores/arrival.py::estimated_arrival(store)` and never typed per store. It returns an
  `ArrivalEstimate(earliest, latest)` whose `.label` ("October 22 – 28") is the only phrasing
  buyers see; `store.arrival` is the template-friendly shortcut. Business days are counted from
  the **close date** (production starts at close, not today), read on the store's own calendar,
  skipping weekends, the eleven observed US federal holidays and `settings.BUSINESS_HOLIDAYS`.
  A store whose close date has passed promises from today, never a date in the past.
  `Store.production_lead_days` / `ship_days_estimate` / `arrival_buffer_days` (the range's
  width) each override `DEFAULT_*` in settings; blank = inherit, like `primary_color`.
  Checkout snapshots the range onto `Order.promised_arrival_earliest`/`_latest` the way line
  prices are snapshotted — the confirmation email leaves the outbox after the store has closed
  and must quote what that buyer was shown, so read `order.promised_arrival`, not the store's.
- **Fulfillment.** `Store.fulfillment_mode` has three values, and they are three different
  products: `individual_ship` (to each buyer, postage absorbed into item prices),
  `group_ship` (one consignment to the organization; each buyer pays a flat share staff quote
  from ShipStation into `group_ship_fee`) and `group_delivery` (we drive a few boxes to a local
  school — one flat fee for the whole drop-off, billed to the organization, never charged to a
  buyer and never sent to Stripe). `store.is_group` covers the last two; ask
  `store.buyer_delivery_fee` and `store.organization_delivery_fee` rather than reading the mode
  inline. A flat per-store fee can never be charged per buyer: while a store is open there's no
  way to know how many buyers will split it.
  Group stores collect `OrderItem.recipient_label` per cart line, so identical items stay
  separate rows there (individual ship still merges them); checkout refuses until every line is
  named. `Order.delivery_fee` is snapshotted at checkout like the line prices, becomes its own
  Stripe line item, and is written by `recalculate()` alongside the total.
  Console "Revenue" sums `subtotal`, not `total`, so delivery never inflates it.
  Live ShipStation rates are deliberately not built: they need a buyer postal address (checkout
  collects none) and package weight (the catalog has no physical attributes), so they wait on
  the synced catalog models.
- **Share kit.** `stores/services/share_kit.py` builds a store's QR (PNG + SVG), US Letter
  flyer (ReportLab), 1080x1080 social image (Pillow) and the copy-paste text. The console
  calls `generate()` and the future client portal will call the same function — neither owns
  the logic. `fingerprint()` hashes only what's printed, so `lifecycle_tick` can offer every
  open store a rebuild each run and do nothing unless something moved; it sweeps **open**
  stores only, because a closed store's arrival promise is recomputed from today and its
  fingerprint would drift daily. Generation failures are caught per store and counted in the
  tick's summary line, so a bad logo never looks like a dead cron. `manage.py
  generate_share_kits [--store slug] [--force]` is the manual wrapper.
  Typefaces come from the Bitstream Vera files inside reportlab — don't commit a font or rely
  on the server having one. Assets live under `stores/<slug>/share/`; regeneration deletes the
  old file first, since storage never overwrites and would otherwise pile up suffixed copies.
- The buyer store page carries Open Graph tags so a pasted link renders as a card. The image is
  served by `stores.views.share_image` at `/<slug>/share-image.png`, never a media URL: those
  are signed and expire in an hour, and a shared link is re-scraped days later.
- Tests that write files use `TempMediaMixin` from `stores/factories.py`, or the suite litters
  the project's `media/` directory.
- Staff never use `/django-admin/`; anything staff need goes in `console/`.
- Static files use ManifestStaticFilesStorage when `DEBUG` is off (plain storage in dev/tests,
  which have no manifest): after template/CSS changes, rebuild Tailwind and run collectstatic
  (deploy.sh does both).
- Write migrations by hand when renaming fields; keep help_text on model fields.
- `orders.models.mark_paid()` is the only way into "paid": a conditional UPDATE, so of the success
  page and the webhook only one wins, and only the winner queues the confirmation email.
- Order status moves go through `orders.models.change_status()`: it enforces
  `ALLOWED_TRANSITIONS` (paid -> sent_to_ops/cancelled, sent_to_ops -> fulfilled/cancelled),
  requires a note to cancel, logs an `OrderStatusChange`, and never touches amounts.
- Console order lists (global and per-store) share `console.views.filter_orders()`; the ops CSV
  uses it too, so filters stay consistent. That CSV's column order is what staff hand-key from —
  don't reorder it (new columns go on the end), and keep the utf-8-sig BOM so Excel opens it cleanly.
- The console store page's Pack-out tab (group stores only) gathers every paid item under its
  `recipient_label` via `console.views.packout_groups()`, printable: printing hides the console
  chrome with Tailwind `print:` utilities rather than rendering a second page.
- Blank `ProductVariant.sku` is filled from `<sku_prefix>-<COLOR>-<SIZE>` by `console.forms.unique_sku()`.
- Tests use the builders in `stores/factories.py` (`make_owner`, `make_manager`, `make_staff`, …).

## Staff accounts
- Roles live on `StaffProfile`: **owner** (everything, including other owners), **manager**
  (everything except owners' accounts and handing out the owner role), **staff** (clients,
  stores, orders, products — no team access).
- Every rule about who may change whom is in `console/permissions.py` and returns
  `(allowed, reason)`; views show that reason verbatim. Don't re-check roles inline.
  Templates get `can_manage_team` from `console.context_processors.console_flags`.
- Accounts are deactivated (`User.is_active`), never deleted — orders and status history
  point at them. Nobody deactivates themselves; the last active owner can't be demoted or
  switched off. Status on the team list is derived: active / invited (no password yet) /
  deactivated.
- Staff sign in with their **email address** (case-insensitive, `ConsoleAuthenticationForm`).
  The username is internal — generated at invitation, never shown or typed. An address two
  accounts share signs nobody in; `console/checks.py` warns (in `migrate`/`check` output) about
  active staff with no email or a shared one, since they're locked out. `/django-admin/` still
  uses Django's username login.
- Invitations: the account is created switched off with an unusable password and a username
  built from the email address; the emailed link is a `TimestampSigner` token that lasts 72
  hours (`console/invitations.py`). Setting a password spends the invitation, so a link can't
  be reused; re-sending mints a fresh one. Expired/used/tampered links get a friendly page.
- Forgotten passwords use Django's own reset views with console templates, and never reveal
  whether an address exists.

## Email
- `MAILERS` is built from the environment in `config/settings.py`: `EMAIL_URL`
  (`smtp://user:pass@host:port/`, `smtps://` for SSL, `?tls=0` to disable STARTTLS) or
  `EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/`EMAIL_USE_TLS`/`EMAIL_USE_SSL`.
  With none set, dev prints to the console and production writes files to `../logs/emails/`
  so nothing is lost before a provider exists. `DEFAULT_FROM_EMAIL` is the shop address.
- Django 6.1 deprecated `EMAIL_BACKEND`/`get_connection()` in favour of `MAILERS`; configure
  mailers, not `EMAIL_BACKEND`.
- Email templates are `.txt` + `.html` pairs extending `console/email/base.*` (rendered by
  `console.mail.render_email()`). `brand_name`/`brand_color` in the context rebrand the header
  and buttons; the text base turns autoescaping off. No logos: S3 media URLs expire in an hour.
- **Outbox pattern — never send email inline in a web request.** Call
  `messaging.outbox.enqueue(kind, to, subject, template_name, context, related=...)`, which
  renders both bodies now and stores an `OutboxEmail` row. `manage.py send_outbox` (cron, every
  minute) sends up to 50 due rows oldest first, each locked with `select_for_update` and
  re-checked so overlapping runs can't double-send; failures back off 1m/5m/15m/1h and the 5th
  is final. Delivery is at-least-once (a crash after SMTP accepts can resend). Staff watch and
  retry at `/console/emails/`. The one exception is the invitation/password-reset mail,
  still sent inline by `send_console_email()` since the person is waiting for it.
- Links in email use `SITE_URL` (there's no request under cron). Links to a buyer's order use
  `orders.links.order_url()`: the order page otherwise only opens in the browser that checked out.

## Commands
- Run locally: `source ../venv/bin/activate && python manage.py runserver`
- Tests: `python manage.py test` — add tests alongside new features; run the suite before committing
- Rebuild CSS: `../bin/tailwindcss -i assets/input.css -o stores/static/stores/site.css --minify`
- Deploy (on server, as root): `/srv/storefront/app/deploy/deploy.sh`
- Send queued email now: `python manage.py send_outbox` (cron does this every minute)
- Apply the store schedule now: `python manage.py lifecycle_tick` (cron: every 5 minutes)

## Cron
- Scheduled jobs live in `deploy/cron.d/storefront`; log rotation in `deploy/logrotate.d/storefront`.
  `deploy.sh` installs both to `/etc/cron.d/` and `/etc/logrotate.d/` (root-owned, 644) on every
  deploy, so edit them in the repo, never on the server. Jobs run as `storefront` via the venv,
  wrapped in `flock -n`, logging to `/srv/storefront/logs/cron-<job>.log`.
- Jobs: `send_outbox` every minute, `lifecycle_tick` every 5 minutes. Each prints one summary
  line per run and then touches `RUN_DIR/<job>.heartbeat` (`/srv/storefront/run/`); the
  dashboard's System box (owners/managers) reads those. Tests that run the commands use
  `TempRunDirMixin` from `stores/factories.py`.
- `deploy.sh` also installs `deploy/cloudwatch-agent.json` (logs -> `/storefront/prod`, 30 days)
  and re-applies it only when it changed; the alarm itself is set up by hand per `MONITORING.md`.

## Not yet built
2FA, buyer accounts,
order hand-off API to the ops system (separate business — never share DB/S3).
