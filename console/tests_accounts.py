from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from console.mail import send_console_email
from console.models import StaffProfile, profile_for
from console.permissions import (
    KEEP_AN_OWNER,
    NOT_TEAM_MANAGER,
    NOT_YOURSELF,
    OWNER_MAKES_OWNER,
    OWNER_ONLY,
    Role,
    can_change_role,
    can_edit,
    can_invite,
    can_manage_team,
    can_set_active,
    is_last_active_owner,
    role_of,
)
from stores.factories import make_manager, make_owner, make_staff


class ConsoleEmailTests(TestCase):
    def test_sends_a_plain_text_and_html_pair_from_the_shop_address(self):
        send_console_email(
            "Test subject",
            "invitation",
            {"invitee_name": "Dana", "inviter_name": "Charlie", "url": "https://example.com/invite/abc/", "site_url": "https://example.com"},
            "dana@example.com",
        )
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Test subject")
        self.assertEqual(message.to, ["dana@example.com"])
        self.assertEqual(message.from_email, "Inked Graphics Stores <orders@inkedgraphics.com>")
        self.assertIn("https://example.com/invite/abc/", message.body)
        self.assertNotIn("<", message.body.split("https://")[0])  # the text part stays plain

        html, content_type = message.alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn("Inked Graphics", html)
        self.assertIn("https://example.com/invite/abc/", html)


class PermissionHelperTests(TestCase):
    """The rules every team view leans on, checked directly."""

    def setUp(self):
        self.owner = make_owner()
        self.second_owner = make_owner()
        self.manager = make_manager()
        self.staffer = make_staff()

    def test_only_owners_and_managers_manage_the_team(self):
        self.assertTrue(can_manage_team(self.owner))
        self.assertTrue(can_manage_team(self.manager))
        self.assertFalse(can_manage_team(self.staffer))

    def test_a_manager_cannot_touch_an_owner(self):
        self.assertTrue(can_edit(self.manager, self.staffer)[0])
        self.assertFalse(can_edit(self.manager, self.owner)[0])
        self.assertEqual(can_edit(self.manager, self.owner)[1], OWNER_ONLY)
        self.assertTrue(can_edit(self.owner, self.second_owner)[0])

    def test_staff_cannot_edit_anyone(self):
        allowed, reason = can_edit(self.staffer, self.staffer)
        self.assertFalse(allowed)
        self.assertEqual(reason, NOT_TEAM_MANAGER)

    def test_only_an_owner_can_create_or_promote_owners(self):
        self.assertFalse(can_change_role(self.manager, self.staffer, Role.OWNER)[0])
        self.assertEqual(can_change_role(self.manager, self.staffer, Role.OWNER)[1], OWNER_MAKES_OWNER)
        self.assertTrue(can_change_role(self.owner, self.staffer, Role.OWNER)[0])
        self.assertFalse(can_invite(self.manager, Role.OWNER)[0])
        self.assertTrue(can_invite(self.manager, Role.STAFF)[0])
        self.assertTrue(can_invite(self.owner, Role.OWNER)[0])
        self.assertFalse(can_invite(self.staffer, Role.STAFF)[0])

    def test_nobody_can_deactivate_themselves(self):
        for user in (self.owner, self.manager):
            with self.subTest(role=role_of(user)):
                allowed, reason = can_set_active(user, user, False)
                self.assertFalse(allowed)
                self.assertEqual(reason, NOT_YOURSELF)

    def test_reactivating_yourself_is_not_blocked_by_that_rule(self):
        self.assertTrue(can_set_active(self.owner, self.owner, True)[0])

    def test_the_last_active_owner_is_protected(self):
        self.second_owner.is_active = False
        self.second_owner.save(update_fields=["is_active"])

        self.assertTrue(is_last_active_owner(self.owner))
        for new_role in (Role.MANAGER, Role.STAFF):
            with self.subTest(new_role=new_role):
                allowed, reason = can_change_role(self.owner, self.owner, new_role)
                self.assertFalse(allowed)
                self.assertEqual(reason, KEEP_AN_OWNER)
        # Another owner can't be deactivated into nothing either.
        allowed, reason = can_set_active(self.second_owner, self.owner, False)
        self.assertFalse(allowed)
        self.assertEqual(reason, KEEP_AN_OWNER)

    def test_with_two_active_owners_either_can_be_changed(self):
        self.assertFalse(is_last_active_owner(self.owner))
        self.assertTrue(can_change_role(self.owner, self.second_owner, Role.MANAGER)[0])
        self.assertTrue(can_set_active(self.owner, self.second_owner, False)[0])


class StaffProfileTests(TestCase):
    def test_a_profile_appears_with_every_staff_user(self):
        user = get_user_model().objects.create_user("newstaff", password="x-pass-7781", is_staff=True)
        self.assertEqual(user.staff_profile.role, Role.STAFF)

    def test_buyers_get_no_profile(self):
        user = get_user_model().objects.create_user("buyer", password="x-pass-7781")
        self.assertFalse(StaffProfile.objects.filter(user=user).exists())

    def test_status_follows_the_user_row(self):
        user = make_staff()
        self.assertEqual(user.staff_profile.status_label, "Active")

        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertEqual(profile_for(user).status_label, "Deactivated")

        invited = make_staff()
        invited.is_active = False
        invited.set_unusable_password()
        invited.save()
        self.assertEqual(profile_for(invited).status_label, "Invited")

    def test_display_name_falls_back_to_the_username(self):
        user = make_staff(username="dana", first_name="Dana", last_name="Whitfield")
        self.assertEqual(user.staff_profile.display_name, "Dana Whitfield")
        self.assertEqual(make_staff(username="nameless").staff_profile.display_name, "nameless")
