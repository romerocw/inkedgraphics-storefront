import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from catalog.models import Product, ProductVariant, StoreProduct
from orders.models import Order
from stores.models import TIME_ZONES, Client, Store

from .models import StaffProfile, profile_for
from .permissions import can_change_role, can_invite

INPUT = "mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 focus:border-brand focus:outline-none"


class StyledFieldsMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.FileInput)):
                field.widget.attrs.setdefault("class", INPUT)


class SectionedFormMixin:
    """Groups a long form under headings. SECTIONS is ((heading, (field name, ...)), ...).

    Any field left out of SECTIONS still renders, in a trailing group of its own — adding a
    model field and forgetting to place it must not make it silently disappear from the form.
    """

    SECTIONS = ()

    def sections(self):
        if not self.SECTIONS:
            return [(None, list(self))]
        grouped, placed = [], set()
        for heading, names in self.SECTIONS:
            fields = [self[name] for name in names if name in self.fields]
            placed.update(names)
            if fields:
                grouped.append((heading, fields))
        unplaced = [bound for bound in self if bound.name not in placed]
        if unplaced:
            grouped.append((None, unplaced))
        return grouped


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


class WallClockDateTimeField(forms.DateTimeField):
    """A date and time exactly as typed, with no time zone yet; the form attaches one.

    Django's own field reads typed times in the site's zone, but a store's times are meant
    in the store's zone, which is picked in the same form.
    """

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, datetime.datetime):
            return value
        return forms.fields.BaseTemporalField.to_python(self, value)


class StoreForm(SectionedFormMixin, StyledForm):
    DATE_FIELDS = ("opens_at", "closes_at")
    SECTIONS = (
        ("Store", ("client", "name", "status", "subdomain", "primary_color")),
        ("Schedule", ("time_zone", "opens_at", "closes_at")),
        ("Arrival promise", ("production_lead_days", "ship_days_estimate", "arrival_buffer_days")),
        ("Fulfillment", (
            "fulfillment_mode", "delivery_location_name", "delivery_address",
            "delivery_contact_name", "delivery_contact_phone", "group_ship_fee", "group_delivery_fee",
        )),
    )
    # Which fulfillment modes each field belongs to; the form hides the rest as the mode changes.
    MODE_FIELDS = {
        "delivery_location_name": Store.GROUP_MODES,
        "delivery_address": Store.GROUP_MODES,
        "delivery_contact_name": Store.GROUP_MODES,
        "delivery_contact_phone": Store.GROUP_MODES,
        "group_ship_fee": (Store.Fulfillment.GROUP_SHIP,),
        "group_delivery_fee": (Store.Fulfillment.GROUP_DELIVERY,),
    }

    class Meta:
        model = Store
        fields = [
            "client", "name", "status", "time_zone", "opens_at", "closes_at",
            "production_lead_days", "ship_days_estimate", "arrival_buffer_days",
            "fulfillment_mode", "delivery_location_name", "delivery_address",
            "delivery_contact_name", "delivery_contact_phone",
            "group_ship_fee", "group_delivery_fee",
            "primary_color", "subdomain",
        ]
        field_classes = {"opens_at": WallClockDateTimeField, "closes_at": WallClockDateTimeField}
        labels = {
            "primary_color": "Store color (overrides client color)",
            "time_zone": "Store time zone",
            "opens_at": "Opens at (store time)",
            "closes_at": "Closes at (store time)",
            "production_lead_days": "Production days (blank = site default)",
            "ship_days_estimate": "Shipping days (blank = site default)",
            "arrival_buffer_days": "Arrival range width (blank = site default)",
            "fulfillment_mode": "How the order reaches buyers",
            "delivery_location_name": "Delivery location",
            "delivery_address": "Delivery address",
            "delivery_contact_name": "Delivery contact",
            "delivery_contact_phone": "Delivery contact phone",
            "group_ship_fee": "Group-ship delivery charged to each buyer",
            "group_delivery_fee": "Group-delivery fee billed to the organization",
        }
        widgets = {
            "opens_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "closes_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "primary_color": forms.TextInput(attrs={"type": "color", "class": "mt-1 h-10 w-20"}),
            "delivery_address": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        given = kwargs.get("initial") or {}
        super().__init__(*args, **kwargs)
        for f in self.DATE_FIELDS:
            self.fields[f].input_formats = ["%Y-%m-%dT%H:%M"]
            # Show saved times on the store's clock, not the site's or the viewer's.
            value = getattr(self.instance, f)
            if value and f not in given:
                self.initial[f] = value.astimezone(self.instance.zone).replace(tzinfo=None)
        if not self.instance.pk:
            self.fields["primary_color"].initial = ""
        for name, modes in self.MODE_FIELDS.items():
            self.fields[name].widget.attrs["data-modes"] = " ".join(modes)

    def clean(self):
        cleaned = super().clean()
        zone_name = cleaned.get("time_zone") or self.instance.time_zone
        zone = ZoneInfo(zone_name)
        zone_label = dict(TIME_ZONES).get(zone_name, zone_name)
        for f in self.DATE_FIELDS:
            typed = cleaned.get(f)
            if typed is None or typed.tzinfo is not None:
                continue
            moment = typed.replace(tzinfo=zone)
            if moment.astimezone(datetime.timezone.utc).astimezone(zone).replace(tzinfo=None) != typed:
                self.add_error(f, f"{typed:%-I:%M %p} doesn't happen that day in {zone_label} time — the clocks skip it. Pick a time an hour later.")
                continue
            cleaned[f] = moment
        opens, closes = cleaned.get("opens_at"), cleaned.get("closes_at")
        if opens and closes and opens.tzinfo and closes.tzinfo and closes <= opens:
            self.add_error("closes_at", "The store has to close after it opens.")

        mode, status = cleaned.get("fulfillment_mode"), cleaned.get("status")
        # A draft can be half-filled, but once a store can be reached by buyers its confirmation
        # email has to be able to tell them where to collect.
        if mode in Store.GROUP_MODES and status != Store.Status.DRAFT:
            if not (cleaned.get("delivery_address") or "").strip():
                self.add_error(
                    "delivery_address",
                    "A group store needs the address its order is delivered to — it's what buyers "
                    "are told at checkout and in their confirmation email.",
                )
        # Money typed against the wrong mode is silently ignored everywhere else, so say so here.
        if mode != Store.Fulfillment.GROUP_SHIP and cleaned.get("group_ship_fee"):
            self.add_error(
                "group_ship_fee",
                "Only a group-ship store charges buyers for delivery. Clear this or change the mode.",
            )
        if mode != Store.Fulfillment.GROUP_DELIVERY and cleaned.get("group_delivery_fee"):
            self.add_error(
                "group_delivery_fee",
                "Only a group-delivery store bills the organization for the drop-off. "
                "Clear this or change the mode.",
            )
        return cleaned

    def save(self, commit=True):
        if not self.instance.slug:
            self.instance.slug = self._unique_slug(self.cleaned_data["name"])
        return super().save(commit)


class ConsolePasswordChangeForm(StyledFieldsMixin, PasswordChangeForm):
    pass


class ConsoleSetPasswordForm(StyledFieldsMixin, SetPasswordForm):
    pass


class ConsolePasswordResetForm(StyledFieldsMixin, PasswordResetForm):
    pass


def user_for_email(email):
    """The one account with this email address (any case), or None if there's none or several."""
    matches = list(get_user_model()._default_manager.filter(email__iexact=email.strip())[:2])
    return matches[0] if len(matches) == 1 else None


class ConsoleAuthenticationForm(StyledFieldsMixin, AuthenticationForm):
    """Staff sign in with their email address. The username is internal and never typed."""

    # AuthenticationForm calls this field "username"; LoginView and the template rely on that.
    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}),
    )
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "That email address and password don't match. Check them and try again — the password is case-sensitive.",
    }
    deactivated = "This account has been deactivated. Ask an owner or manager to turn it back on."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # AuthenticationForm sizes this field for usernames (150); emails can run to 254.
        self.fields["username"].max_length = 254
        self.fields["username"].widget.attrs["maxlength"] = 254

    def clean(self):
        email = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if not (email and password):
            return self.cleaned_data

        user = user_for_email(email)
        if user is None:
            # Hash anyway, as Django's own backend does, so an unknown address takes as long
            # to refuse as a wrong password and can't be discovered by timing.
            get_user_model()().set_password(password)
            raise self.get_invalid_login_error()

        self.user_cache = authenticate(self.request, username=user.get_username(), password=password)
        if self.user_cache is None:
            # Only say "deactivated" when the password was right, so the form can't be
            # used to find out which addresses belong to staff.
            if not user.is_active and user.check_password(password):
                raise ValidationError(self.deactivated, code="deactivated")
            raise self.get_invalid_login_error()
        self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class ConsoleStoreProductForm(StyledForm):
    """Inline row for editing what a store sells and for how much."""

    class Meta:
        model = StoreProduct
        fields = ["display_name", "price", "is_active", "sort_order"]
        labels = {"display_name": "Name shown to buyers", "is_active": "For sale", "sort_order": "Order on page"}


StoreProductFormSet = forms.modelformset_factory(StoreProduct, form=ConsoleStoreProductForm, extra=0)


class PickBlankForm(StyledFieldsMixin, forms.Form):
    """Set up one blank for one store: what it's called, what it costs, which colours and sizes.

    Colours and sizes are picked separately and crossed, because that is how someone thinks
    about a school store — "the green one, youth small through adult 2XL" — rather than by
    ticking two hundred individual variants.
    """

    display_name = forms.CharField(
        max_length=200, required=False, label="Name buyers see",
        help_text="Leave blank to use the catalog name.",
    )
    price = forms.DecimalField(
        max_digits=8, decimal_places=2, min_value=0, label="Price buyers pay",
        help_text="Before any size surcharge.",
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Description (optional)",
    )
    image = forms.ImageField(
        required=False, label="Photo (optional)",
        help_text="Your own photo. The supplier's images are missing for most styles.",
    )

    def __init__(self, *args, blank=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.blank = blank
        self.variants = list(blank.variants.filter(is_active=True)) if blank else []
        self.fields["colors"] = forms.MultipleChoiceField(
            choices=[(c, c) for c in self.color_names()], widget=forms.CheckboxSelectMultiple,
            label="Colours this store sells",
        )
        self.fields["sizes"] = forms.MultipleChoiceField(
            choices=[(s, s) for s in self.size_names()], widget=forms.CheckboxSelectMultiple,
            label="Sizes this store sells",
        )
        self.fields["display_name"].initial = blank.buyer_name if blank else ""

    def color_names(self):
        seen = []
        for variant in self.variants:
            if variant.color_name not in seen:
                seen.append(variant.color_name)
        return seen

    def size_names(self):
        ordered = sorted(self.variants, key=lambda v: (v.size_sort_order or 0, v.size))
        seen = []
        for variant in ordered:
            if variant.size and variant.size not in seen:
                seen.append(variant.size)
        return seen

    def swatches(self):
        """(colour, hex, checkbox) so the template can show a real colour next to each box."""
        hexes = {v.color_name: v.color_hex for v in self.variants}
        for box in self["colors"]:
            yield {"name": box.data["value"], "hex": hexes.get(box.data["value"], ""), "box": box}

    def chosen_variants(self):
        """The variants the picked colours and sizes actually cross to."""
        colors = set(self.cleaned_data["colors"])
        sizes = set(self.cleaned_data["sizes"])
        return [v for v in self.variants if v.color_name in colors and v.size in sizes]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("colors") and cleaned.get("sizes") and not self.chosen_variants():
            # e.g. a youth colour that only comes in youth sizes, crossed with adult sizes.
            raise ValidationError(
                "That combination of colours and sizes doesn't exist for this product. "
                "Pick colours and sizes that go together."
            )
        return cleaned


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


class ProductForm(StyledForm):
    class Meta:
        model = Product
        fields = ["name", "sku_prefix", "description", "image", "default_price", "cost", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
        labels = {"sku_prefix": "SKU prefix", "is_active": "In the catalog"}


def unique_sku(sku_prefix, color, size, exclude_pk=None):
    """Build 'PREFIX-COLOR-SIZE', adding -2, -3... if that SKU is already used."""
    base = "-".join(part.strip() for part in (sku_prefix, color, size) if part.strip()).upper().replace(" ", "-")
    base = base or "SKU"
    taken = ProductVariant.objects.exclude(pk=exclude_pk) if exclude_pk else ProductVariant.objects.all()
    sku, n = base, 2
    while taken.filter(sku=sku).exists():
        sku, n = f"{base}-{n}", n + 1
    return sku


class ProductVariantForm(StyledForm):
    """One size/color row. A blank SKU is filled in from the product's prefix."""

    class Meta:
        model = ProductVariant
        fields = ["color", "size", "sku", "upcharge", "is_active", "sort_order"]
        labels = {"is_active": "For sale", "sort_order": "Order"}

    def __init__(self, *args, sku_prefix="", **kwargs):
        super().__init__(*args, **kwargs)
        self.sku_prefix = sku_prefix
        self.fields["sku"].required = False
        self.fields["sku"].help_text = "Leave blank to build it from the SKU prefix, color and size."

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("sku"):
            cleaned["sku"] = unique_sku(
                self.sku_prefix, cleaned.get("color", ""), cleaned.get("size", ""), exclude_pk=self.instance.pk
            )
        return cleaned


ProductVariantFormSet = forms.inlineformset_factory(
    Product, ProductVariant, form=ProductVariantForm, extra=2, can_delete=True
)


class ProductFilterForm(StyledFieldsMixin, forms.Form):
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(attrs={"placeholder": "Product name or SKU prefix"}))
    active = forms.ChoiceField(
        required=False, label="In the catalog",
        choices=[("", "Active and inactive"), ("1", "Active only"), ("0", "Inactive only")],
    )


class MyAccountForm(StyledFieldsMixin, forms.ModelForm):
    """Someone editing their own details. Changing the email needs the current password."""

    phone = forms.CharField(max_length=30, required=False, label="Phone", help_text="Where colleagues can reach you.")
    current_password = forms.CharField(
        required=False, widget=forms.PasswordInput,
        label="Your current password",
        help_text="Only needed if you're changing your email address.",
    )

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email"]
        labels = {"first_name": "First name", "last_name": "Last name", "email": "Email address"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["phone"].initial = profile_for(self.instance).phone

    def clean_email(self):
        email = self.cleaned_data["email"]
        if get_user_model()._default_manager.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Another account already uses that email address.")
        return email

    def clean(self):
        cleaned = super().clean()
        email_changed = "email" in cleaned and cleaned["email"].lower() != (self.initial.get("email") or "").lower()
        if email_changed and not self.instance.check_password(cleaned.get("current_password") or ""):
            self.add_error("current_password", "Please enter your current password to change your email address.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit)
        profile = profile_for(user)
        profile.phone = self.cleaned_data["phone"]
        profile.save(update_fields=["phone"])
        return user


def unique_username(email):
    """Sign-in name from the email's first part: dana@… -> dana, then dana2, dana3…"""
    base = slugify(email.split("@")[0].replace(".", "-")) or "staff"
    users = get_user_model()._default_manager
    username, n = base, 2
    while users.filter(username=username).exists():
        username, n = f"{base}{n}", n + 1
    return username


class TeamInviteForm(StyledFieldsMixin, forms.Form):
    """Invite a colleague. Creates the account switched off until they set a password."""

    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    email = forms.EmailField(label="Email address", help_text="Where we'll send the invitation.")
    role = forms.ChoiceField(choices=StaffProfile.Role.choices, initial=StaffProfile.Role.STAFF, label="Role")
    job_title = forms.CharField(max_length=100, required=False, label="Job title")

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor

    def clean_email(self):
        email = self.cleaned_data["email"]
        if get_user_model()._default_manager.filter(email__iexact=email).exists():
            raise ValidationError("Someone with that email address already has an account.")
        return email

    def clean_role(self):
        role = self.cleaned_data["role"]
        allowed, reason = can_invite(self.actor, role)
        if not allowed:
            raise ValidationError(reason)
        return role

    def save(self):
        """Create the switched-off account. The caller emails the invitation."""
        user = get_user_model()._default_manager.create_user(
            unique_username(self.cleaned_data["email"]),
            email=self.cleaned_data["email"],
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
            is_staff=True,
            is_active=False,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        profile = profile_for(user)
        profile.role = self.cleaned_data["role"]
        profile.job_title = self.cleaned_data["job_title"]
        profile.invited_by = self.actor
        profile.save(update_fields=["role", "job_title", "invited_by"])
        return user


class TeamMemberForm(StyledFieldsMixin, forms.ModelForm):
    """Edit a colleague's details and role."""

    role = forms.ChoiceField(choices=StaffProfile.Role.choices, label="Role")
    job_title = forms.CharField(max_length=100, required=False, label="Job title")

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email"]
        labels = {"first_name": "First name", "last_name": "Last name", "email": "Email address"}

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["email"].required = True
        profile = profile_for(self.instance)
        self.fields["role"].initial = profile.role
        self.fields["job_title"].initial = profile.job_title

    def clean_email(self):
        email = self.cleaned_data["email"]
        if get_user_model()._default_manager.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Another account already uses that email address.")
        return email

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role != profile_for(self.instance).role:
            allowed, reason = can_change_role(self.actor, self.instance, role)
            if not allowed:
                raise ValidationError(reason)
        return role

    def save(self, commit=True):
        user = super().save(commit)
        profile = profile_for(user)
        profile.role = self.cleaned_data["role"]
        profile.job_title = self.cleaned_data["job_title"]
        profile.save(update_fields=["role", "job_title"])
        return user
