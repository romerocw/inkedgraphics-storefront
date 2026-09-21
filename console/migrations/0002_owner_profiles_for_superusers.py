from django.db import migrations


def give_superusers_owner_profiles(apps, schema_editor):
    """The accounts that existed before roles came along are the owners."""
    User = apps.get_model("auth", "User")
    StaffProfile = apps.get_model("console", "StaffProfile")
    for user in User.objects.filter(is_superuser=True):
        StaffProfile.objects.update_or_create(user=user, defaults={"role": "owner"})


def do_nothing(apps, schema_editor):
    """Rolling back leaves the profiles alone; they're harmless without the role UI."""


class Migration(migrations.Migration):

    dependencies = [
        ('console', '0001_staffprofile'),
    ]

    operations = [
        migrations.RunPython(give_superusers_owner_profiles, do_nothing),
    ]
