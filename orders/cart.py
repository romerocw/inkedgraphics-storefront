import secrets
from decimal import Decimal

from catalog.models import BlankVariant, ProductVariant, StoreProduct

SESSION_KEY = "cart"


class Cart:
    """Session-backed cart. Holds lines for one store at a time."""

    def __init__(self, request):
        self.session = request.session
        data = self.session.get(SESSION_KEY)
        if not data:
            data = self.session[SESSION_KEY] = {"store_id": None, "lines": {}}
        self.data = data

    @property
    def store_id(self):
        return self.data.get("store_id")

    def add(self, store_product, variant, qty, separate_line=False):
        if self.store_id and self.store_id != store_product.store_id:
            self.data["lines"] = {}
        self.data["store_id"] = store_product.store_id
        kind = "blank" if isinstance(variant, BlankVariant) else "product"
        key = f"{store_product.id}:{kind}:{variant.id}"
        if separate_line:
            # Group stores name a recipient per line, so the same hoodie ordered for a second
            # child stays its own row instead of bumping the first one's quantity.
            key = f"{key}:{secrets.token_hex(3)}"
        line = self.data["lines"].setdefault(
            key, {"sp": store_product.id, "v": variant.id, "kind": kind, "qty": 0},
        )
        line["qty"] += qty
        self.save()

    def set_label(self, key, label):
        """Record who a line is for. Ignored for lines that aren't in the cart any more."""
        if key in self.data["lines"]:
            self.data["lines"][key]["label"] = label.strip()[:120]
            self.save()

    def set_qty(self, key, qty):
        if key in self.data["lines"]:
            if qty <= 0:
                del self.data["lines"][key]
            else:
                self.data["lines"][key]["qty"] = qty
            self.save()

    def clear(self):
        self.session.pop(SESSION_KEY, None)
        self.session.modified = True

    def save(self):
        self.session.modified = True

    def __len__(self):
        return sum(line["qty"] for line in self.data["lines"].values())

    def items(self):
        lines = self.data["lines"]
        products = StoreProduct.objects.select_related("product", "blank").in_bulk(
            [l["sp"] for l in lines.values()]
        )
        variants = {
            "blank": BlankVariant.objects.in_bulk(
                [l["v"] for l in lines.values() if l.get("kind") == "blank"]
            ),
            # Lines put in the cart before the ops sync, and any legacy store product.
            "product": ProductVariant.objects.in_bulk(
                [l["v"] for l in lines.values() if l.get("kind", "product") != "blank"]
            ),
        }
        for key, line in lines.items():
            sp = products.get(line["sp"])
            variant = variants[line.get("kind", "product")].get(line["v"])
            if not sp or not variant:
                continue
            unit = sp.price_for(variant)
            yield {
                "key": key, "store_product": sp, "variant": variant, "qty": line["qty"],
                "label": line.get("label", ""),
                "unit_price": unit, "line_total": unit * line["qty"],
            }

    def total(self):
        return sum((i["line_total"] for i in self.items()), Decimal("0"))
