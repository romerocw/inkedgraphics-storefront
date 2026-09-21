"""Console email. One branded pair of templates per message: plain text and HTML."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def site_url(request):
    """Absolute base URL for links in emails, e.g. https://store.inkedgraphics.com."""
    return f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"


def render_email(template_name, context):
    """Render <template_name>.txt and .html; returns (text, html)."""
    text = render_to_string(f"{template_name}.txt", context).strip() + "\n"
    html = render_to_string(f"{template_name}.html", context)
    return text, html


def send_console_email(subject, template, context, to):
    """Render console/email/<template>.txt and .html and send them to one address."""
    text, html = render_email(f"console/email/{template}", context)
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    message.attach_alternative(html, "text/html")
    message.send()
    return message
