"""Startup warnings for staff accounts that can't sign in.

Console sign-in is by email address, so an active staff account with no email — or one
sharing its address with another account — is locked out. `migrate` runs these (deploy.sh
shows them), so a missing address surfaces before someone finds out at the login page.
"""

from collections import Counter

from django.contrib.auth import get_user_model
from django.core.checks import Warning, register
from django.db import DatabaseError


@register("console")
def staff_can_sign_in(app_configs, databases=None, **kwargs):
    if not databases:  # only when a command asks for database checks, as migrate does
        return []
    try:
        staff = list(get_user_model()._default_manager.filter(is_staff=True, is_active=True).only("username", "email"))
    except DatabaseError:  # a fresh database before its first migrate
        return []

    warnings = [
        Warning(
            f'Staff account "{user.get_username()}" has no email address, so it can\'t sign in to the console.',
            hint="Give it one in /django-admin/ or with: manage.py shell -c \"from django.contrib.auth.models import User; "
            f"u = User.objects.get(username='{user.get_username()}'); u.email = 'you@example.com'; u.save()\"",
            id="console.W001",
        )
        for user in staff
        if not (user.email or "").strip()
    ]
    shared = Counter(user.email.strip().lower() for user in staff if (user.email or "").strip())
    warnings += [
        Warning(
            f"Several staff accounts use {email}, so none of them can sign in to the console with it.",
            hint="Give each account its own email address.",
            id="console.W002",
        )
        for email, count in shared.items()
        if count > 1
    ]
    return warnings
