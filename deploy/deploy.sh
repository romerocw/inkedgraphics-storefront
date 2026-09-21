#!/usr/bin/env bash
# Deploy the storefront: pull from GitHub, install, build, migrate, restart.
# Run on the server as root:  /srv/storefront/app/deploy/deploy.sh
set -euo pipefail
BASE=/srv/storefront
APP=$BASE/app
PY=$BASE/venv/bin/python
run() { sudo -u storefront "$@"; }

echo "== pull";        run git -C "$APP" pull --ff-only
echo "== pip";         run $BASE/venv/bin/pip install -q -r "$APP/requirements.txt"
echo "== tailwind";    run $BASE/bin/tailwindcss -i "$APP/assets/input.css" -o "$APP/stores/static/stores/site.css" --minify
echo "== migrate";     run $PY "$APP/manage.py" migrate --noinput
echo "== collectstatic"; run $PY "$APP/manage.py" collectstatic --noinput | tail -1
echo "== restart";     systemctl restart storefront-uwsgi
sleep 2
echo "== health";      curl -sf http://127.0.0.1/health/ && echo
echo "== deployed $(run git -C "$APP" rev-parse --short HEAD)"
