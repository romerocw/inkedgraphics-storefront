"""Invitation links: a signed, timestamped token that stops working after 3 days.

The token carries nothing but the user's id, and an invitation is spent as soon as the
person sets a password — so a link can't be used twice even while it's still in date.
"""

from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import reverse

from .mail import send_console_email, site_url

SALT = "console.invitation"
MAX_AGE = 72 * 60 * 60  # three days, as the emails promise


class InvitationInvalid(Exception):
    """This link can't be used, and we can say why in plain English."""

    message = "That invitation link doesn't look right. Ask an owner or manager to send you a new one."


class InvitationExpired(InvitationInvalid):
    message = "That invitation has expired — invitations last 3 days. Ask an owner or manager for a new one."


class InvitationUsed(InvitationInvalid):
    message = "That invitation has already been used. You can sign in below, or reset your password."


def make_token(user):
    return signing.TimestampSigner(salt=SALT).sign(str(user.pk))


def user_from_token(token):
    """The invited user, or an InvitationInvalid subclass explaining what went wrong."""
    try:
        user_pk = signing.TimestampSigner(salt=SALT).unsign(token, max_age=MAX_AGE)
    except signing.SignatureExpired:
        raise InvitationExpired
    except signing.BadSignature:
        raise InvitationInvalid

    user = get_user_model()._default_manager.filter(pk=user_pk, is_staff=True).first()
    if user is None:
        raise InvitationInvalid
    if user.has_usable_password():
        raise InvitationUsed
    return user


def invitation_url(request, user):
    return request.build_absolute_uri(reverse("console:invitation_accept", args=[make_token(user)]))


def send_invitation(request, user, invited_by):
    """Email the invitation. Safe to call again to re-send: the link is made fresh."""
    return send_console_email(
        "You've been invited to the Inked Graphics console",
        "invitation",
        {
            "invitee_name": user.get_short_name() or "",
            "inviter_name": invited_by.get_full_name() or invited_by.email or "A colleague",
            "url": invitation_url(request, user),
            "site_url": site_url(request),
        },
        user.email,
    )
