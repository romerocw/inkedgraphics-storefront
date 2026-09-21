#!/usr/bin/env bash
# Deploy the storefront: pull from GitHub, install, build, migrate, restart.
# Run on the server as root:  /srv/storefront/app/deploy/deploy.sh
set -euo pipefail
BASE=/srv/storefront
APP=$BASE/app
PY=$BASE/venv/bin/python
run() { sudo -u storefront "$@"; }

# The tailwind step below rewrites this tracked file, which would make the next pull abort.
echo "== reset css";   run git -C "$APP" checkout -- stores/static/stores/site.css
echo "== pull";        run git -C "$APP" pull --ff-only
echo "== pip";         run $BASE/venv/bin/pip install -q -r "$APP/requirements.txt"
echo "== tailwind";    run $BASE/bin/tailwindcss -i "$APP/assets/input.css" -o "$APP/stores/static/stores/site.css" --minify
echo "== migrate";     run $PY "$APP/manage.py" migrate --noinput
echo "== collectstatic"; run $PY "$APP/manage.py" collectstatic --noinput | tail -1
# Scheduled jobs and log rotation are versioned in deploy/; cron ignores files it can't trust.
echo "== cron";        install -m 644 -o root -g root "$APP/deploy/cron.d/storefront" /etc/cron.d/storefront
echo "== logrotate";   install -m 644 -o root -g root "$APP/deploy/logrotate.d/storefront" /etc/logrotate.d/storefront
echo "== restart";     systemctl restart storefront-uwsgi
sleep 2
echo "== health";      curl -sf http://127.0.0.1/health/ && echo

# Ship logs to CloudWatch (deploy/MONITORING.md). Last, so a monitoring problem never blocks
# the app itself. Re-applied only when the config changed; a failed apply removes the
# installed copy so the next deploy tries again.
CWA=/opt/aws/amazon-cloudwatch-agent
CWA_CONF=$CWA/etc/storefront-cloudwatch-agent.json
echo "== cloudwatch"
if [ ! -x "$CWA/bin/amazon-cloudwatch-agent-ctl" ]; then
  echo "WARNING: CloudWatch agent not installed; logs are NOT being shipped. See deploy/MONITORING.md."
elif cmp -s "$APP/deploy/cloudwatch-agent.json" "$CWA_CONF"; then
  echo "unchanged"
else
  install -m 644 -o root -g root "$APP/deploy/cloudwatch-agent.json" "$CWA_CONF"
  "$CWA/bin/amazon-cloudwatch-agent-ctl" -a fetch-config -m ec2 -s -c "file:$CWA_CONF" \
    || { rm -f "$CWA_CONF"; echo "ERROR: CloudWatch agent rejected the config; the app is deployed, logs are not shipping."; exit 1; }
fi

echo "== deployed $(run git -C "$APP" rev-parse --short HEAD)"
