from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


def default_from_email():
    return settings.DEFAULT_FROM_EMAIL


class OutboxEmail(models.Model):
    """One email waiting to go out, or the record of one that went (or couldn't).

    Web requests only ever add rows here (messaging.outbox.enqueue); the send_outbox
    command, run by cron every minute, does the actual sending.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=50, help_text="What sort of email this is, e.g. order_confirmation. For reporting.")
    to_email = models.EmailField(help_text="Recipient address.")
    to_name = models.CharField(max_length=200, blank=True, help_text="Recipient name, shown in the To: header if given.")
    from_email = models.CharField(max_length=254, default=default_from_email, help_text="Sender, e.g. 'Inked Graphics Stores <orders@…>'.")
    subject = models.CharField(max_length=255)
    text_body = models.TextField(help_text="Plain-text version, rendered when the email was queued.")
    html_body = models.TextField(blank=True, help_text="HTML version, rendered when the email was queued.")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0, help_text="How many times sending has been tried.")
    last_error = models.TextField(blank=True, help_text="What went wrong on the most recent failed attempt.")
    next_attempt_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When a failed email will be tried again. Blank once it's sent or we've given up.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    related_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Type of the record this email is about, e.g. an order.",
    )
    related_id = models.PositiveBigIntegerField(null=True, blank=True, help_text="ID of the record this email is about.")
    related = GenericForeignKey("related_type", "related_id")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="outbox_due"),
            models.Index(fields=["related_type", "related_id"], name="outbox_related"),
        ]

    def __str__(self):
        return f"{self.kind} to {self.to_email} ({self.status})"

    @property
    def gave_up(self):
        return self.status == self.Status.FAILED and self.next_attempt_at is None
