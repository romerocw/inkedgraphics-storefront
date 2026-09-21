from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.utils.text import slugify

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
