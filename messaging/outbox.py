"""The outbox: queue email now, send it from cron (manage.py send_outbox).

Nothing in a web request talks to the mail server. Callers use enqueue(), which renders
the templates straight away (so the email says what was true at the time) and stores a row.
"""

from datetime import timedelta
from email.utils import formataddr

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.mail import EmailMultiAlternatives
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from console.mail import render_email

from .models import OutboxEmail


def enqueue(kind, to, subject, template_name, context, related=None):
    """Render <template_name>.txt and .html and queue the result for one recipient.

    `to` is an address or a (name, address) pair. `related` is the record the email is
    about (e.g. an order), so it can be found again from that record. Templates get
    `site_url` (settings.SITE_URL) unless the context sets it.
    """
    to_name, to_email = to if isinstance(to, (tuple, list)) else ("", to)
    text, html = render_email(template_name, {"site_url": settings.SITE_URL, **context})
    return OutboxEmail.objects.create(
        kind=kind,
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        text_body=text,
        html_body=html,
        related_type=ContentType.objects.get_for_model(related) if related is not None else None,
        related_id=related.pk if related is not None else None,
    )


def emails_about(obj):
    """Every outbox row about one record, newest first."""
    return OutboxEmail.objects.filter(related_type=ContentType.objects.get_for_model(obj), related_id=obj.pk)


# --- sending (called by manage.py send_outbox) ---

BATCH_SIZE = 50
MAX_ATTEMPTS = 5
# Wait after failed attempt 1, 2, 3, 4 before trying again. Attempt 5 failing is final.
BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1)]


def due(now=None):
    """Rows that should be tried now: queued ones, and failed ones whose wait is over."""
    now = now or timezone.now()
    return OutboxEmail.objects.filter(
        Q(status=OutboxEmail.Status.QUEUED)
        | Q(status=OutboxEmail.Status.FAILED, next_attempt_at__lte=now, attempts__lt=MAX_ATTEMPTS)
    )


def claim(pk):
    """Lock one due row for this run, or None if another run has it or already sent it.

    The row stays locked until the caller's transaction ends, so two overlapping runs can
    never both send it. Where the database can skip locked rows we do; otherwise the second
    run waits, then sees the row is no longer due.
    """
    skip_locked = connection.features.has_select_for_update_skip_locked
    return due().select_for_update(skip_locked=skip_locked).filter(pk=pk).first()


def build_message(email):
    to = formataddr((email.to_name, email.to_email)) if email.to_name else email.to_email
    message = EmailMultiAlternatives(email.subject, email.text_body, email.from_email, [to])
    if email.html_body:
        message.attach_alternative(email.html_body, "text/html")
    return message


def send_one(email):
    """Send one claimed row through the default mailer and record what happened."""
    now = timezone.now()
    email.attempts += 1
    try:
        build_message(email).send()
    except Exception as error:  # anything the mailer raises is a failed attempt, not a crash
        email.status = OutboxEmail.Status.FAILED
        email.last_error = f"{type(error).__name__}: {error}"[:2000]
        email.next_attempt_at = now + BACKOFF[email.attempts - 1] if email.attempts < MAX_ATTEMPTS else None
        sent = False
    else:
        email.status = OutboxEmail.Status.SENT
        email.sent_at = now
        email.last_error = ""
        email.next_attempt_at = None
        sent = True
    email.save(update_fields=["status", "attempts", "last_error", "next_attempt_at", "sent_at"])
    return sent


def send_due(limit=BATCH_SIZE):
    """Send up to `limit` due emails, oldest first. Returns (sent, failed, remaining)."""
    sent = failed = 0
    for pk in list(due().order_by("created_at", "pk").values_list("pk", flat=True)[:limit]):
        with transaction.atomic():
            email = claim(pk)
            if email is None:
                continue
            if send_one(email):
                sent += 1
            else:
                failed += 1
    return sent, failed, due().count()


def retry(email):
    """Staff asked to try a failed email again: queue it now with a fresh set of attempts."""
    email.status = OutboxEmail.Status.QUEUED
    email.attempts = 0
    email.next_attempt_at = None
    email.save(update_fields=["status", "attempts", "next_attempt_at"])
    return email
