from django.db import models


class Client(models.Model):
    """An organization we run a private-labeled store for (school, department, business)."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, help_text="Used in URLs; lowercase, no spaces.")
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Store(models.Model):
    """A single group store with an open/close window."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="stores")
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, help_text="Store URL path, e.g. store.inkedgraphics.com/<slug>/")
    subdomain = models.CharField(
        max_length=63, blank=True, null=True, unique=True,
        help_text="Optional: <subdomain>.inkedgraphics.com for clients who want their own host.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client} — {self.name}"
