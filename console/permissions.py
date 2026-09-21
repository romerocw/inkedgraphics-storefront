"""Who may do what to staff accounts.

Views ask these questions instead of checking roles inline. Each answer is a
(allowed, reason) pair so the reason can go straight to the person as a message.
"""

from .models import StaffProfile, profile_for

Role = StaffProfile.Role
TEAM_MANAGERS = [Role.OWNER, Role.MANAGER]
ALLOWED = (True, "")

NOT_TEAM_MANAGER = "Only owners and managers can change staff accounts."
OWNER_ONLY = "Only an owner can change another owner's account."
OWNER_MAKES_OWNER = "Only an owner can make someone else an owner."
KEEP_AN_OWNER = "Someone has to stay an owner. Make someone else an owner first."
NOT_YOURSELF = "You can't deactivate your own account. Ask another owner or manager to do it."


def role_of(user):
    return profile_for(user).role if getattr(user, "is_authenticated", False) else None


def can_manage_team(user):
    """Can this person see and change the team list at all?"""
    return bool(getattr(user, "is_staff", False)) and role_of(user) in TEAM_MANAGERS


def is_last_active_owner(user):
    return role_of(user) == Role.OWNER and not (
        StaffProfile.objects.filter(role=Role.OWNER, user__is_active=True).exclude(user=user).exists()
    )


def can_edit(actor, target):
    """Change a colleague's name, email or phone."""
    if not can_manage_team(actor):
        return False, NOT_TEAM_MANAGER
    if role_of(target) == Role.OWNER and role_of(actor) != Role.OWNER:
        return False, OWNER_ONLY
    return ALLOWED


def can_change_role(actor, target, new_role):
    allowed, reason = can_edit(actor, target)
    if not allowed:
        return False, reason
    if new_role == Role.OWNER and role_of(actor) != Role.OWNER:
        return False, OWNER_MAKES_OWNER
    if new_role != Role.OWNER and is_last_active_owner(target):
        return False, KEEP_AN_OWNER
    return ALLOWED


def can_set_active(actor, target, active):
    """Deactivate (active=False) or reactivate (active=True) a colleague."""
    allowed, reason = can_edit(actor, target)
    if not allowed:
        return False, reason
    if not active:
        if actor.pk == target.pk:
            return False, NOT_YOURSELF
        if is_last_active_owner(target):
            return False, KEEP_AN_OWNER
    return ALLOWED


def can_invite(actor, new_role):
    if not can_manage_team(actor):
        return False, NOT_TEAM_MANAGER
    if new_role == Role.OWNER and role_of(actor) != Role.OWNER:
        return False, OWNER_MAKES_OWNER
    return ALLOWED
