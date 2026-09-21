"""The outbox: queue email now, send it from cron (manage.py send_outbox).

Nothing in a web request talks to the mail server. Callers use enqueue(), which renders
the templates straight away (so the email says what was true at the time) and stores a row.
"""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType

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
