from django.conf import settings
from django.db import models


class StaffProfile(models.Model):
    """The extra details we keep about a member of staff, beyond Django's User."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        STAFF = "staff", "Staff"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.STAFF,
        help_text="Owners can manage everyone. Managers can manage staff. Staff run stores and orders.",
    )
    phone = models.CharField(max_length=30, blank=True, help_text="Where to reach this person about an order.")
    job_title = models.CharField(max_length=100, blank=True, help_text="Shown on the team list, e.g. 'Production lead'.")
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="invitations_sent", help_text="Who invited this person. Blank for the original accounts.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name", "user__username"]

    def __str__(self):
        return f"{self.user.get_username()} ({self.get_role_display()})"

    # Account status is derived, so it can never disagree with the User row:
    # an invited person is switched on only once they've set a password.
    @property
    def is_invited(self):
        return not self.user.is_active and not self.user.has_usable_password()

    @property
    def status(self):
        if self.user.is_active:
            return "active"
        return "invited" if self.is_invited else "deactivated"

    @property
    def status_label(self):
        return {"active": "Active", "invited": "Invited", "deactivated": "Deactivated"}[self.status]

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.get_username()


def profile_for(user):
    """Every staff user has a profile; make one the first time we need it."""
    profile, _ = StaffProfile.objects.get_or_create(user=user)
    return profile
