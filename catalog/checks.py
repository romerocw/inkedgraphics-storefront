"""Startup warnings about the link to the ops catalog API.

`migrate` and `check` run these, and deploy.sh shows their output, so a half-set ops link
surfaces on the deploy that caused it rather than the next time the sync runs.

Silence is the normal state: with neither setting present there's simply no ops link yet.
"""

from django.conf import settings
from django.core.checks import Warning, register

SETTINGS_HINT = (
    "Set both in the .env one level above manage.py (/srv/storefront/.env on the server), "
    "then restart: sudo systemctl restart storefront-uwsgi"
)


@register("catalog")
def ops_api_is_configured(app_configs, **kwargs):
    url = (getattr(settings, "OPS_API_URL", "") or "").strip()
    token = (getattr(settings, "OPS_API_TOKEN", "") or "").strip()
    if not url and not token:
        return []

    warnings = []
    if url and not token:
        warnings.append(Warning(
            "OPS_API_URL is set but OPS_API_TOKEN is empty, so calls to the ops catalog API "
            "would be rejected.",
            hint=SETTINGS_HINT,
            id="catalog.W001",
        ))
    if token and not url:
        warnings.append(Warning(
            "OPS_API_TOKEN is set but OPS_API_URL is empty, so there's nowhere to send it.",
            hint=SETTINGS_HINT,
            id="catalog.W002",
        ))
    if url and not url.startswith("https://"):
        warnings.append(Warning(
            f"OPS_API_URL is {url!r}, which isn't https — the service token would cross the "
            "network in clear text.",
            hint="Use an https:// URL unless you're pointing at a local test server.",
            id="catalog.W003",
        ))
    return warnings
