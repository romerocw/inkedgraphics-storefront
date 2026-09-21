# Inked Graphics Storefront

Django 6 app for private-labeled group stores (schools, teams, businesses). Buyers shop a
time-limited store, pay via Stripe Checkout; staff manage clients/stores in a console.

## Layout
- `config/` settings, urls, health-check middleware (`/health/`)
- `stores/` Client and Store models, public store pages, base templates
- `catalog/` Product, ProductVariant (size/color, `upcharge`), StoreProduct (per-store price)
- `orders/` Order/OrderItem (prices snapshotted), session Cart, checkout, Stripe (`payments.py`)
- `console/` staff UI at `/console/` (login required, `is_staff`)
  - stores: list -> store page (`?tab=summary|products|orders`), edit at `stores/<pk>/edit/`
  - orders: `orders/` (filters, 50/page), `orders/<order_number>/`, CSV at `stores/<pk>/orders.csv`
  - catalog: `products/`, `products/new/`, `products/<pk>/` (product + variants in one form)
  - accounts: `profile/` (own details), `password/`, `team/` + `team/<pk>/` (owner/manager only)
  - public pages: `login/`, `forgot/` (4 reset steps), `invite/<token>/`
  - `StaffProfile` (role, phone, job title, invited_by), `permissions.py`, `invitations.py`, `mail.py`
- `assets/input.css` Tailwind v4 source -> built to `stores/static/stores/site.css`
- `deploy/` `uwsgi.ini`, `deploy.sh`

## Conventions
- Settings read `../.env` (one level above manage.py); never commit `.env`.
- Local dev: `DB_ENGINE=sqlite`, `DEBUG=1`, local media. Prod: MariaDB, S3 media, Stripe keys.
- Cart holds one store at a time. Order totals come from `Order.recalculate()`.
- `Store.primary_color` overrides `Client.primary_color`; blank = inherit.
- Staff never use `/django-admin/`; anything staff need goes in `console/`.
- Static files use ManifestStaticFilesStorage when `DEBUG` is off (plain storage in dev/tests,
  which have no manifest): after template/CSS changes, rebuild Tailwind and run collectstatic
  (deploy.sh does both).
- Write migrations by hand when renaming fields; keep help_text on model fields.
- Order status moves go through `orders.models.change_status()`: it enforces
  `ALLOWED_TRANSITIONS` (paid -> sent_to_ops/cancelled, sent_to_ops -> fulfilled/cancelled),
  requires a note to cancel, logs an `OrderStatusChange`, and never touches amounts.
- Console order lists (global and per-store) share `console.views.filter_orders()`; the ops CSV
  uses it too, so filters stay consistent. That CSV's column order is what staff hand-key from —
  don't reorder it, and keep the utf-8-sig BOM so Excel opens it cleanly.
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
- Send console email through `console.mail.send_console_email()`, which renders a
  `console/email/<name>.txt` + `.html` pair off one branded base — reuse it for order
  confirmations.

## Commands
- Run locally: `source ../venv/bin/activate && python manage.py runserver`
- Tests: `python manage.py test` — add tests alongside new features; run the suite before committing
- Rebuild CSS: `../bin/tailwindcss -i assets/input.css -o stores/static/stores/site.css --minify`
- Deploy (on server, as root): `/srv/storefront/app/deploy/deploy.sh`

## Not yet built
Order confirmation emails, store lifecycle cron, 2FA, buyer accounts,
order hand-off API to the ops system (separate business — never share DB/S3).
