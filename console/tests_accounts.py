import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

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


class LoginPageTests(TestCase):
    def setUp(self):
        self.user = make_staff(username="dana", password="right-pass-5512")

    def test_the_page_offers_a_way_out_of_a_forgotten_password(self):
        response = self.client.get(reverse("console:login"))
        self.assertContains(response, reverse("console:password_reset"))
        self.assertContains(response, "Forgot your password?")

    def test_a_wrong_password_says_nothing_about_the_account(self):
        response = self.client.post(reverse("console:login"), {"username": "dana", "password": "wrong"})
        self.assertContains(response, "Please enter a correct username and password")
        self.assertNotContains(response, "deactivated")

    def test_an_unknown_username_says_nothing_about_the_account(self):
        response = self.client.post(reverse("console:login"), {"username": "nobody", "password": "whatever"})
        self.assertContains(response, "Please enter a correct username and password")

    def test_a_deactivated_user_with_the_right_password_is_told_why(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(reverse("console:login"), {"username": "dana", "password": "right-pass-5512"})
        self.assertContains(response, "has been deactivated")

    def test_a_deactivated_user_with_a_wrong_password_learns_nothing(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(reverse("console:login"), {"username": "dana", "password": "wrong"})
        self.assertNotContains(response, "deactivated")

    def test_signing_in_works_and_lands_on_the_dashboard(self):
        response = self.client.post(
            reverse("console:login"), {"username": "dana", "password": "right-pass-5512"}, follow=True
        )
        self.assertRedirects(response, reverse("console:dashboard"))

    def test_signing_out_says_so_on_the_login_page(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("console:logout"), follow=True)
        self.assertRedirects(response, reverse("console:login"))
        self.assertContains(response, "You&#x27;ve been signed out")


class PasswordResetFlowTests(TestCase):
    def setUp(self):
        self.user = make_staff(username="dana", password="old-pass-9271", email="dana@inkedgraphics.com")

    def test_the_whole_flow_from_request_to_signing_in_again(self):
        sent = self.client.post(reverse("console:password_reset"), {"email": "dana@inkedgraphics.com"})
        self.assertRedirects(sent, reverse("console:password_reset_sent"))
        self.assertContains(self.client.get(reverse("console:password_reset_sent")), "Check your email")

        message = mail.outbox[0]
        self.assertEqual(message.to, ["dana@inkedgraphics.com"])
        self.assertEqual(message.subject, "Set a new password for your Inked Graphics account")
        link = re.search(r"https?://[^\s]+/console/reset/[^\s]+", message.body).group(0)
        html, _ = message.alternatives[0]
        self.assertIn(link, html)  # both parts point at the same link

        path = link.split("testserver", 1)[1]
        follow = self.client.get(path, follow=True)  # Django swaps the token for a session-held one
        self.assertContains(follow, "Set a new password")

        done = self.client.post(
            follow.redirect_chain[-1][0], {"new_password1": "brand-new-7741", "new_password2": "brand-new-7741"}
        )
        self.assertRedirects(done, reverse("console:password_reset_done"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brand-new-7741"))
        self.assertTrue(self.client.login(username="dana", password="brand-new-7741"))

    def test_an_unknown_address_looks_exactly_the_same_and_emails_nobody(self):
        response = self.client.post(reverse("console:password_reset"), {"email": "stranger@example.com"})
        self.assertRedirects(response, reverse("console:password_reset_sent"))
        self.assertEqual(mail.outbox, [])

    def test_a_deactivated_account_gets_no_reset_email(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.client.post(reverse("console:password_reset"), {"email": "dana@inkedgraphics.com"})
        self.assertEqual(mail.outbox, [])

    def test_a_used_link_stops_working(self):
        self.client.post(reverse("console:password_reset"), {"email": "dana@inkedgraphics.com"})
        link = re.search(r"https?://[^\s]+/console/reset/[^\s]+", mail.outbox[0].body).group(0)
        path = link.split("testserver", 1)[1]
        confirm = self.client.get(path, follow=True)
        self.client.post(
            confirm.redirect_chain[-1][0], {"new_password1": "brand-new-7741", "new_password2": "brand-new-7741"}
        )

        again = self.client.get(path)
        self.assertFalse(again.context["validlink"])
        self.assertContains(again, "That link has expired")
        self.assertContains(again, reverse("console:password_reset"))

    def test_a_made_up_link_is_refused_kindly(self):
        response = self.client.get(reverse("console:password_reset_confirm", args=["MQ", "made-up-token"]))
        self.assertFalse(response.context["validlink"])
        self.assertContains(response, "That link has expired")


class MyAccountTests(TestCase):
    def setUp(self):
        self.user = make_staff(username="dana", password="right-pass-5512", email="dana@inkedgraphics.com")
        self.client.force_login(self.user)

    def post(self, **overrides):
        data = {
            "first_name": "Dana", "last_name": "Whitfield",
            "email": "dana@inkedgraphics.com", "phone": "703-555-0101", "current_password": "",
        }
        data.update(overrides)
        return self.client.post(reverse("console:my_account"), data, follow=True)

    def test_name_and_phone_save_without_a_password(self):
        response = self.post()
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.last_name), ("Dana", "Whitfield"))
        self.assertEqual(profile_for(self.user).phone, "703-555-0101")
        self.assertContains(response, "Your details have been saved")

    def test_changing_the_email_needs_the_current_password(self):
        response = self.post(email="new@inkedgraphics.com")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "dana@inkedgraphics.com")
        self.assertContains(response, "enter your current password")

    def test_changing_the_email_works_with_the_current_password(self):
        self.post(email="new@inkedgraphics.com", current_password="right-pass-5512")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@inkedgraphics.com")

    def test_a_wrong_current_password_is_refused(self):
        self.post(email="new@inkedgraphics.com", current_password="not-my-password")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "dana@inkedgraphics.com")

    def test_an_email_another_account_uses_is_refused(self):
        make_staff(username="sam", email="sam@inkedgraphics.com")
        response = self.post(email="sam@inkedgraphics.com", current_password="right-pass-5512")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "dana@inkedgraphics.com")
        self.assertContains(response, "already uses that email")

    def test_the_page_links_to_the_password_page_and_shows_the_role(self):
        response = self.client.get(reverse("console:my_account"))
        self.assertContains(response, reverse("console:password_change"))
        self.assertContains(response, "Staff")

    def test_staff_of_any_role_can_reach_their_own_account(self):
        for user in (make_owner(), make_manager(), make_staff()):
            with self.subTest(role=role_of(user)):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("console:my_account")).status_code, 200)

    def test_changing_the_password_comes_back_to_my_account(self):
        response = self.client.post(
            reverse("console:password_change"),
            {"old_password": "right-pass-5512", "new_password1": "fresh-pass-8823", "new_password2": "fresh-pass-8823"},
        )
        self.assertRedirects(response, reverse("console:my_account"))
