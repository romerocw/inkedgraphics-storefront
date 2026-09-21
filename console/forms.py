from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.utils.text import slugify

from catalog.models import StoreProduct
from orders.models import Order
from stores.models import Client, Store

INPUT = "mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 focus:border-brand focus:outline-none"


class StyledFieldsMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.FileInput)):
                field.widget.attrs.setdefault("class", INPUT)


class StyledForm(StyledFieldsMixin, forms.ModelForm):
    def _unique_slug(self, source):
        base = slugify(source) or "item"
        slug, n = base, 2
        qs = self._meta.model.objects.exclude(pk=self.instance.pk)
        while qs.filter(slug=slug).exists():
            slug, n = f"{base}-{n}", n + 1
        return slug


class ClientForm(StyledForm):
    class Meta:
        model = Client
        fields = ["name", "contact_name", "contact_email", "primary_color", "logo", "notes"]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color", "class": "mt-1 h-10 w-20"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def save(self, commit=True):
        if not self.instance.slug:
            self.instance.slug = self._unique_slug(self.cleaned_data["name"])
        return super().save(commit)


class StoreForm(StyledForm):
    class Meta:
        model = Store
        fields = ["client", "name", "status", "opens_at", "closes_at", "primary_color", "subdomain"]
        widgets = {
            "opens_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "closes_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "primary_color": forms.TextInput(attrs={"type": "color", "class": "mt-1 h-10 w-20"}),
        }
        labels = {"primary_color": "Store color (overrides client color)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("opens_at", "closes_at"):
            self.fields[f].input_formats = ["%Y-%m-%dT%H:%M"]
        if not self.instance.pk:
            self.fields["primary_color"].initial = ""

    def save(self, commit=True):
        if not self.instance.slug:
            self.instance.slug = self._unique_slug(self.cleaned_data["name"])
        return super().save(commit)


class ConsolePasswordChangeForm(StyledFieldsMixin, PasswordChangeForm):
    pass


class ConsoleStoreProductForm(StyledForm):
    """Inline row for editing what a store sells and for how much."""

    class Meta:
        model = StoreProduct
        fields = ["display_name", "price", "is_active", "sort_order"]
        labels = {"display_name": "Name shown to buyers", "is_active": "For sale", "sort_order": "Order on page"}


StoreProductFormSet = forms.modelformset_factory(StoreProduct, form=ConsoleStoreProductForm, extra=0)


class AddStoreProductsForm(StyledFieldsMixin, forms.Form):
    """Checkbox + price per catalog product a store doesn't offer yet."""

    def __init__(self, *args, products=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.products = list(products)
        for product in self.products:
            self.fields[f"add_{product.pk}"] = forms.BooleanField(required=False, label=product.name)
            self.fields[f"price_{product.pk}"] = forms.DecimalField(
                max_digits=8, decimal_places=2, min_value=0, required=False,
                initial=product.default_price, label=f"Price for {product.name}",
            )
        for field in self.fields.values():
            if isinstance(field, forms.DecimalField):
                field.widget.attrs["class"] = INPUT

    def rows(self):
        for product in self.products:
            yield {"product": product, "add": self[f"add_{product.pk}"], "price": self[f"price_{product.pk}"]}

    def chosen(self, everything=False):
        """(product, price) pairs staff asked for. A blank price means the product's usual price."""
        for product in self.products:
            if everything or self.cleaned_data.get(f"add_{product.pk}"):
                price = self.cleaned_data.get(f"price_{product.pk}")
                yield product, product.default_price if price is None else price


class OrderFilterForm(StyledFieldsMixin, forms.Form):
    """The filter bar above an order list. Everything is optional."""

    PAID_GROUP = ""
    ALL = "all"
    STATUS_CHOICES = [
        (PAID_GROUP, "Paid, in production & fulfilled"),
        (ALL, "Every status"),
    ] + list(Order.Status.choices)

    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(attrs={"placeholder": "Order number, buyer or recipient"}))
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES, label="Status")
    store = forms.ModelChoiceField(required=False, queryset=Store.objects.select_related("client"), label="Store", empty_label="Every store")

    def __init__(self, *args, with_store=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not with_store:
            del self.fields["store"]


class OrderStatusForm(forms.Form):
    """One status move, checked against the rules in orders.models."""

    to_status = forms.ChoiceField(choices=Order.Status.choices)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "class": INPUT}))
