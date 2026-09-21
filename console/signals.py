from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import profile_for


@receiver(post_save, sender=get_user_model(), dispatch_uid="console.create_staff_profile")
def create_staff_profile(sender, instance, **kwargs):
    """Keep a profile alongside every staff user, including ones made by createsuperuser."""
    if instance.is_staff:
        profile_for(instance)
