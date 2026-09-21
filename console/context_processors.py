"""Console-wide template flags, so templates never work out roles for themselves."""

from .permissions import can_manage_team


def console_flags(request):
    user = getattr(request, "user", None)
    return {"can_manage_team": bool(user and user.is_authenticated and can_manage_team(user))}
