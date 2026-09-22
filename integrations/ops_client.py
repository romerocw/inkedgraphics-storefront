"""HTTP client for the ops catalog API (API A).

The contract is docs/ops-storefront-api.md; this file implements §3 and §4 of it. Nothing here
knows what a blank is — it fetches pages and turns failures into two kinds of error, because
that is the only distinction a caller needs:

    OpsTemporaryError  the call may work later (429, 5xx, timeouts) — back off and retry
    OpsPermanentError  it will not (400, 401, 403, 404) — stop and tell someone

Both instances sit behind the same ALB, which serves its own HTML error pages when the target
is down or slow. So a response body is never assumed to be JSON.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# §3: "client connect 5s, read 30s".
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class OpsError(Exception):
    """Something went wrong talking to ops."""

    def __init__(self, message, status=None, code="", details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}


class OpsTemporaryError(OpsError):
    """Worth retrying: rate limited, server error, or the connection never completed."""


class OpsPermanentError(OpsError):
    """Not worth retrying: bad request, bad credentials, missing scope, unknown reference."""


def _describe(response):
    """(code, message, details) from a contract error body, or from whatever else arrived.

    A proxy's HTML error page has none of this, so fall back to something a human can act on
    rather than raising a parse error that hides the real status.
    """
    try:
        body = response.json()
    except ValueError:
        snippet = " ".join(response.text.split())[:200]
        return "", snippet or f"HTTP {response.status_code} with an empty body", {}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "", f"HTTP {response.status_code} with an unexpected body", {}
    return error.get("code", ""), error.get("message", ""), error.get("details") or {}


class OpsClient:
    """Reads the ops catalog. One instance per sync run."""

    def __init__(self, base_url=None, token=None, session=None):
        self.base_url = (base_url if base_url is not None else settings.OPS_API_URL).rstrip("/")
        self.token = token if token is not None else settings.OPS_API_TOKEN
        self.session = session or requests.Session()

    @property
    def is_configured(self):
        return bool(self.base_url and self.token)

    def get(self, path_or_url, params=None):
        """GET a contract endpoint and return the decoded JSON body.

        `path_or_url` may be a path or the absolute `next` URL from a list response.
        """
        if not self.is_configured:
            raise OpsPermanentError(
                "The ops API isn't configured: set OPS_API_URL and OPS_API_TOKEN in .env.",
            )
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        try:
            response = self.session.get(
                url,
                params=params,
                headers={"Authorization": f"Token {self.token}", "Accept": "application/json"},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            # No response at all: DNS, refused connection, or a read that ran past the timeout.
            raise OpsTemporaryError(f"Couldn't reach the ops API: {exc}") from exc

        if response.status_code >= 400:
            code, message, details = _describe(response)
            error = OpsTemporaryError if response.status_code in RETRYABLE_STATUSES else OpsPermanentError
            logger.warning("ops API %s %s -> %s %s", "GET", url, response.status_code, code or message)
            raise error(message or code or "Request failed", status=response.status_code, code=code, details=details)

        try:
            return response.json()
        except ValueError as exc:
            # A 200 that isn't JSON means something is answering that isn't the API.
            raise OpsTemporaryError(f"The ops API returned a {response.status_code} that wasn't JSON.") from exc

    def styles(self, updated_since=None, page_size=100):
        """Yield each page of the style list, following `next` until it runs out (§4)."""
        params = {"page_size": page_size}
        if updated_since:
            params["updated_since"] = updated_since
        url, first = "/api/v1/catalog/styles/", True
        while url:
            page = self.get(url, params=params if first else None)
            yield page
            url, first = page.get("next"), False

    def style(self, style_id):
        return self.get(f"/api/v1/catalog/styles/{style_id}/")
