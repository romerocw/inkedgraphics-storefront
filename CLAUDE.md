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
  - own password at `password/`
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
- Tests use the builders in `stores/factories.py`.

## Commands
- Run locally: `source ../venv/bin/activate && python manage.py runserver`
- Tests: `python manage.py test` — add tests alongside new features; run the suite before committing
- Rebuild CSS: `../bin/tailwindcss -i assets/input.css -o stores/static/stores/site.css --minify`
- Deploy (on server, as root): `/srv/storefront/app/deploy/deploy.sh`

## Not yet built
Email (confirmations, magic links), store lifecycle cron, 2FA, buyer accounts,
order hand-off API to the ops system (separate business — never share DB/S3).
