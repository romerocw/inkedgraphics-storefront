"""A fake of the ops catalog API, built from the JSON in docs/ops-storefront-api.md §4.

No test may call the live endpoint (contract §9), so this stands in for it. It speaks the
contract rather than mimicking the client: pages, `server_time`, `next`, and the contract's
error bodies. If the memo's payload changes, this file changes in the same commit.
"""

from integrations.ops_client import OpsPermanentError, OpsTemporaryError

# The style resource exactly as the contract prints it.
CC1567 = {
    "style_id": 407,
    "supplier_style_code": "CC1567",
    "brand": "Comfort Colors",
    "display_title": "COMFORT COLORS Adult Ringspun Hooded Sweatshirt",
    "merch_label": None,
    "category": None,
    "audience": "adult",
    "is_active": True,
    "stock_policy": "job_only",
    "base_cost": {"amount": "25.92", "currency": "USD"},
    "size_chart": None,
    "variants": [
        {
            "color_name": "BlueJean",
            "color_hex": "#596B83",
            "size": "S",
            "size_sort_order": 2,
            "blank_sku": "CC1567-BlueJean-S",
            "cost_adjustment": {"amount": "0.00", "currency": "USD"},
            "is_active": False,
            "stock_policy": "job_only",
        }
    ],
    "images": [
        {
            "color_name": "BlueJean",
            "view": None,
            "image_type": "large",
            "url": "https://www.carolinamade.com/prodimg/large/cc1567BJN_092024112321.png",
        }
    ],
    "updated_at": "2026-09-22T20:37:09Z",
}


def style(style_id, **overrides):
    """A copy of the contract style with fields swapped, for building a catalog."""
    payload = {**CC1567, "style_id": style_id, "supplier_style_code": f"ST{style_id}", **overrides}
    if "variants" not in overrides:
        payload["variants"] = [
            {**variant, "blank_sku": f"ST{style_id}-{variant['color_name']}-{variant['size']}"}
            for variant in CC1567["variants"]
        ]
    return payload


class FakeOpsCatalog:
    """Serves styles the way the contract says ops does.

    The styles themselves live on `catalog`, because `styles()` is the client method this
    stands in for. `page_size` lets a test force a page boundary without inventing a hundred.
    """

    BASE = "https://ops.test"

    def __init__(self, styles=None, page_size=2, server_time="2026-09-22T22:32:33Z"):
        self.catalog = list(styles if styles is not None else [CC1567])
        self.page_size = page_size
        self.server_time = server_time
        self.requests = []
        self.fail_on_page = None
        self.failure = None

    # --- the bits of OpsClient a caller actually uses -------------------------------------
    @property
    def is_configured(self):
        return True

    def _page(self, number):
        start = (number - 1) * self.page_size
        rows = self.catalog[start:start + self.page_size]
        if not rows and number > 1:
            raise OpsPermanentError("Invalid page.", status=404, code="not_found")
        has_more = len(self.catalog) > start + self.page_size
        return {
            "count": len(self.catalog),
            "next": f"{self.BASE}/api/v1/catalog/styles/?page={number + 1}" if has_more else None,
            "previous": None,
            "server_time": self.server_time,
            "results": rows,
        }

    def styles_pages(self, updated_since=None, page_size=100):
        number = 1
        while True:
            self.requests.append({"page": number, "updated_since": updated_since})
            if self.fail_on_page == number:
                raise self.failure or OpsTemporaryError("ops fell over", status=503)
            page = self._page(number)
            yield page
            if not page["next"]:
                return
            number += 1

    # The client's real method name, so the fake is a drop-in.
    def styles(self, updated_since=None, page_size=100):
        return self.styles_pages(updated_since=updated_since, page_size=page_size)

    def break_on(self, page, failure=None):
        """Make page `page` raise, to test that a mid-run failure keeps the old cursor."""
        self.fail_on_page, self.failure = page, failure
        return self
