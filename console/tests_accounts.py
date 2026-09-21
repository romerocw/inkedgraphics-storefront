import re
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from console.checks import staff_can_sign_in
from console.invitations import MAX_AGE
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

    def test_display_name_falls_back_to_the_email_then_the_username(self):
        user = make_staff(username="dana", first_name="Dana", last_name="Whitfield")
        self.assertEqual(user.staff_profile.display_name, "Dana Whitfield")
        self.assertEqual(
            make_staff(username="nameless", email="nameless@inkedgraphics.com").staff_profile.display_name,
            "nameless@inkedgraphics.com",
        )
        self.assertEqual(make_staff(username="no-email", email="").staff_profile.display_name, "no-email")


class LoginPageTests(TestCase):
    """Staff sign in with their email address; the username is internal."""

    NO_MATCH = escape("That email address and password don't match")

    def setUp(self):
        self.user = make_staff(username="dana-whitfield", password="right-pass-5512", email="dana@inkedgraphics.com")

    def sign_in(self, email, password, **kwargs):
        return self.client.post(reverse("console:login"), {"username": email, "password": password}, **kwargs)

    def test_the_page_asks_for_an_email_address(self):
        response = self.client.get(reverse("console:login"))
        self.assertContains(response, "Email address")
        self.assertContains(response, 'type="email"')
        self.assertNotContains(response, "Username")

    def test_the_page_offers_a_way_out_of_a_forgotten_password(self):
        response = self.client.get(reverse("console:login"))
        self.assertContains(response, reverse("console:password_reset"))
        self.assertContains(response, "Forgot your password?")

    def test_signing_in_with_the_email_lands_on_the_dashboard(self):
        response = self.sign_in("dana@inkedgraphics.com", "right-pass-5512", follow=True)
        self.assertRedirects(response, reverse("console:dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_the_email_is_not_case_sensitive(self):
        response = self.sign_in("Dana@InkedGraphics.COM", "right-pass-5512")
        self.assertRedirects(response, reverse("console:dashboard"))

    def test_the_internal_username_is_not_accepted(self):
        response = self.sign_in("dana-whitfield", "right-pass-5512")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_wrong_password_says_nothing_about_the_account(self):
        response = self.sign_in("dana@inkedgraphics.com", "wrong")
        self.assertContains(response, self.NO_MATCH)
        self.assertNotContains(response, "deactivated")

    def test_an_unknown_address_gets_the_same_answer(self):
        response = self.sign_in("nobody@inkedgraphics.com", "whatever")
        self.assertContains(response, self.NO_MATCH)

    def test_an_address_two_accounts_share_signs_nobody_in(self):
        make_staff(email="DANA@inkedgraphics.com", password="right-pass-5512")
        response = self.sign_in("dana@inkedgraphics.com", "right-pass-5512")
        self.assertContains(response, self.NO_MATCH)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_deactivated_user_with_the_right_password_is_told_why(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.sign_in("dana@inkedgraphics.com", "right-pass-5512")
        self.assertContains(response, "has been deactivated")

    def test_a_deactivated_user_with_a_wrong_password_learns_nothing(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.sign_in("dana@inkedgraphics.com", "wrong")
        self.assertNotContains(response, "deactivated")
        self.assertContains(response, self.NO_MATCH)

    def test_signing_out_says_so_on_the_login_page(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("console:logout"), follow=True)
        self.assertRedirects(response, reverse("console:login"))
        self.assertContains(response, "You&#x27;ve been signed out")


class SignInCheckTests(TestCase):
    """The startup warnings that catch staff who'd be locked out of email sign-in."""

    def warnings(self):
        return staff_can_sign_in(None, databases=["default"])

    def test_quiet_when_everyone_has_their_own_address(self):
        make_staff(email="one@inkedgraphics.com")
        make_staff(email="two@inkedgraphics.com")
        self.assertEqual(self.warnings(), [])

    def test_warns_about_active_staff_with_no_email(self):
        make_staff(username="no-email", email="")
        (warning,) = self.warnings()
        self.assertEqual(warning.id, "console.W001")
        self.assertIn('"no-email"', warning.msg)

    def test_ignores_deactivated_accounts_and_non_staff(self):
        gone = make_staff(email="")
        gone.is_active = False
        gone.save(update_fields=["is_active"])
        get_user_model().objects.create_user("buyer", email="", password="x-pass-7781")
        self.assertEqual(self.warnings(), [])

    def test_warns_about_a_shared_address_whatever_its_case(self):
        make_staff(email="shared@inkedgraphics.com")
        make_staff(email="Shared@InkedGraphics.com")
        (warning,) = self.warnings()
        self.assertEqual(warning.id, "console.W002")

    def test_skipped_unless_database_checks_are_asked_for(self):
        make_staff(email="")
        self.assertEqual(staff_can_sign_in(None), [])


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
        signed_in = self.client.post(
            reverse("console:login"), {"username": "dana@inkedgraphics.com", "password": "brand-new-7741"}
        )
        self.assertRedirects(signed_in, reverse("console:dashboard"))

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


class TeamListTests(TestCase):
    def setUp(self):
        self.owner = make_owner(username="olivia", first_name="Olivia", last_name="Owner")
        self.staffer = make_staff(username="sam", first_name="Sam", last_name="Staffer")

    def test_owners_and_managers_see_the_team_and_the_nav_link(self):
        for user in (self.owner, make_manager()):
            with self.subTest(role=role_of(user)):
                self.client.force_login(user)
                response = self.client.get(reverse("console:team"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(self.client.get(reverse("console:dashboard")), reverse("console:team"))

    def test_staff_are_refused_and_never_see_the_link(self):
        self.client.force_login(self.staffer)
        self.assertEqual(self.client.get(reverse("console:team")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("console:dashboard")), reverse("console:team"))

    def test_the_list_shows_names_roles_and_status(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("console:team"))
        self.assertContains(response, "Olivia Owner")
        self.assertContains(response, "Sam Staffer")
        self.assertContains(response, "Never")  # neither has signed in during the test
        self.assertContains(response, "Active")

    def test_search_narrows_the_list(self):
        self.client.force_login(self.owner)
        found = self.client.get(reverse("console:team"), {"q": "sam"}).context["profiles"]
        self.assertEqual([p.user for p in found], [self.staffer])

    def test_an_empty_search_explains_itself(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("console:team"), {"q": "zzz"}), "Nobody matches that search")


class TeamInviteTests(TestCase):
    def setUp(self):
        self.owner = make_owner(username="olivia")
        self.client.force_login(self.owner)

    def invite(self, **overrides):
        data = {"first_name": "Dana", "last_name": "Whitfield", "email": "dana@inkedgraphics.com", "role": Role.STAFF, "job_title": "Production"}
        data.update(overrides)
        return self.client.post(reverse("console:team_invite"), data, follow=True)

    def test_inviting_creates_a_switched_off_account_and_emails_a_link(self):
        response = self.invite()
        user = get_user_model().objects.get(email="dana@inkedgraphics.com")
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_staff)
        self.assertEqual(user.username, "dana")
        profile = profile_for(user)
        self.assertEqual((profile.role, profile.job_title, profile.invited_by), (Role.STAFF, "Production", self.owner))
        self.assertEqual(profile.status_label, "Invited")

        self.assertEqual(mail.outbox[0].to, ["dana@inkedgraphics.com"])
        self.assertIn("/console/invite/", mail.outbox[0].body)
        text, (html, _) = mail.outbox[0].body, mail.outbox[0].alternatives[0]
        for part in (text, html):
            self.assertNotIn("username", part.lower())
            self.assertIn("sign in with this email address", part)
        self.assertContains(response, "Invitation sent to dana@inkedgraphics.com")

    def test_an_address_already_in_use_is_refused(self):
        make_staff(email="dana@inkedgraphics.com")
        response = self.invite()
        self.assertContains(response, "already has an account")
        self.assertEqual(mail.outbox, [])

    def test_a_manager_cannot_invite_an_owner(self):
        self.client.force_login(make_manager())
        response = self.invite(role=Role.OWNER)
        self.assertContains(response, escape(OWNER_MAKES_OWNER))
        self.assertFalse(get_user_model().objects.filter(email="dana@inkedgraphics.com").exists())

    def test_a_manager_can_invite_staff_and_managers(self):
        self.client.force_login(make_manager())
        for role, email in ((Role.STAFF, "one@inkedgraphics.com"), (Role.MANAGER, "two@inkedgraphics.com")):
            with self.subTest(role=role):
                self.invite(role=role, email=email)
                self.assertEqual(profile_for(get_user_model().objects.get(email=email)).role, role)

    def test_staff_cannot_reach_the_invite_page(self):
        self.client.force_login(make_staff())
        self.assertEqual(self.client.get(reverse("console:team_invite")).status_code, 403)
        self.assertEqual(self.client.post(reverse("console:team_invite")).status_code, 403)

    def test_usernames_do_not_collide(self):
        make_staff(username="dana")
        self.invite()
        self.assertEqual(get_user_model().objects.get(email="dana@inkedgraphics.com").username, "dana2")


class TeamMemberTests(TestCase):
    def setUp(self):
        self.owner = make_owner(username="olivia")
        self.second_owner = make_owner(username="oscar")
        self.manager = make_manager(username="maria")
        self.staffer = make_staff(username="sam", first_name="Sam", email="sam@inkedgraphics.com")

    def edit(self, member, **overrides):
        data = {"first_name": "Sam", "last_name": "Staffer", "email": member.email, "role": profile_for(member).role, "job_title": ""}
        data.update(overrides)
        return self.client.post(reverse("console:team_member", args=[member.pk]), data, follow=True)

    def set_active(self, member, active):
        return self.client.post(
            reverse("console:team_member_status", args=[member.pk]), {"active": "1" if active else "0"}, follow=True
        )

    def test_an_owner_can_edit_details_and_role(self):
        self.client.force_login(self.owner)
        response = self.edit(self.staffer, last_name="Staffer", role=Role.MANAGER, job_title="Production lead")
        self.staffer.refresh_from_db()
        self.assertEqual(self.staffer.last_name, "Staffer")
        self.assertEqual(profile_for(self.staffer).role, Role.MANAGER)
        self.assertEqual(profile_for(self.staffer).job_title, "Production lead")
        self.assertContains(response, "Saved Sam Staffer")

    def test_a_manager_cannot_open_or_change_an_owner(self):
        self.client.force_login(self.manager)
        opened = self.client.get(reverse("console:team_member", args=[self.owner.pk]), follow=True)
        self.assertRedirects(opened, reverse("console:team"))
        self.assertContains(opened, escape(OWNER_ONLY))

        self.edit(self.owner, first_name="Renamed")
        self.owner.refresh_from_db()
        self.assertNotEqual(self.owner.first_name, "Renamed")

    def test_a_manager_cannot_promote_someone_to_owner(self):
        self.client.force_login(self.manager)
        response = self.edit(self.staffer, role=Role.OWNER)
        self.assertEqual(profile_for(self.staffer).role, Role.STAFF)
        self.assertContains(response, escape(OWNER_MAKES_OWNER))

    def test_staff_cannot_reach_the_member_pages(self):
        self.client.force_login(self.staffer)
        for name in ("team_member", "team_member_status", "team_resend_invite"):
            with self.subTest(page=name):
                url = reverse(f"console:{name}", args=[self.owner.pk])
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)

    def test_deactivating_stops_sign_in_but_keeps_the_account(self):
        self.client.force_login(self.owner)
        response = self.set_active(self.staffer, False)
        self.staffer.refresh_from_db()
        self.assertFalse(self.staffer.is_active)
        self.assertTrue(get_user_model().objects.filter(pk=self.staffer.pk).exists())
        self.assertEqual(profile_for(self.staffer).status_label, "Deactivated")
        self.assertContains(response, "can no longer sign in")

        back = self.set_active(self.staffer, True)
        self.staffer.refresh_from_db()
        self.assertTrue(self.staffer.is_active)
        self.assertContains(back, "can sign in again")

    def test_nobody_deactivates_themselves(self):
        self.client.force_login(self.owner)
        response = self.set_active(self.owner, False)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertContains(response, escape(NOT_YOURSELF))

    def test_a_manager_cannot_deactivate_an_owner(self):
        self.client.force_login(self.manager)
        response = self.set_active(self.owner, False)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertContains(response, escape(OWNER_ONLY))

    def test_the_last_active_owner_cannot_be_demoted_or_switched_off(self):
        self.second_owner.is_active = False
        self.second_owner.save(update_fields=["is_active"])
        self.client.force_login(self.owner)

        demoted = self.edit(self.owner, first_name="Olivia", email=self.owner.email, role=Role.MANAGER)
        self.assertEqual(profile_for(self.owner).role, Role.OWNER)
        self.assertContains(demoted, escape(KEEP_AN_OWNER))

        # A second owner exists again, so now it's allowed.
        self.set_active(self.second_owner, True)
        self.edit(self.owner, first_name="Olivia", email=self.owner.email, role=Role.MANAGER)
        self.assertEqual(profile_for(self.owner).role, Role.MANAGER)

    def test_an_email_another_account_uses_is_refused(self):
        self.client.force_login(self.owner)
        response = self.edit(self.staffer, email=self.manager.email)
        self.staffer.refresh_from_db()
        self.assertEqual(self.staffer.email, "sam@inkedgraphics.com")
        self.assertContains(response, "already uses that email")

    def test_the_page_hides_deactivation_when_it_is_not_allowed(self):
        self.client.force_login(self.owner)
        own_page = self.client.get(reverse("console:team_member", args=[self.owner.pk]))
        self.assertFalse(own_page.context["can_deactivate"])
        self.assertContains(own_page, escape(NOT_YOURSELF))

        others = self.client.get(reverse("console:team_member", args=[self.staffer.pk]))
        self.assertTrue(others.context["can_deactivate"])
        self.assertContains(others, "Deactivate this account")


class InvitationLifecycleTests(TestCase):
    def setUp(self):
        self.owner = make_owner(username="olivia", first_name="Olivia")
        self.client.force_login(self.owner)
        self.client.post(
            reverse("console:team_invite"),
            {"first_name": "Dana", "last_name": "Whitfield", "email": "dana@inkedgraphics.com", "role": Role.STAFF, "job_title": ""},
        )
        self.invited = get_user_model().objects.get(email="dana@inkedgraphics.com")
        self.link = re.search(r"https?://[^\s]+/console/invite/[^\s]+", mail.outbox[0].body).group(0)
        self.path = self.link.split("testserver", 1)[1]
        self.client.logout()

    def accept(self, path=None, password="chosen-pass-4417"):
        return self.client.post(path or self.path, {"new_password1": password, "new_password2": password}, follow=True)

    def test_accepting_sets_the_password_switches_the_account_on_and_signs_them_in(self):
        page = self.client.get(self.path)
        self.assertContains(page, "Set your password")
        self.assertContains(page, "You'll sign in with <strong>dana@inkedgraphics.com</strong>")

        response = self.accept()
        self.invited.refresh_from_db()
        self.assertTrue(self.invited.is_active)
        self.assertTrue(self.invited.check_password("chosen-pass-4417"))
        self.assertRedirects(response, reverse("console:dashboard"))
        self.assertContains(response, "Welcome to Inked Graphics, Dana Whitfield")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.invited.pk)

    def test_a_used_link_cannot_be_used_again(self):
        self.accept()
        self.client.logout()
        again = self.client.get(self.path)
        self.assertEqual(again.status_code, 400)
        self.assertContains(again, "already been used", status_code=400)
        self.assertContains(again, reverse("console:password_reset"), status_code=400)

    def test_an_expired_link_says_so_and_offers_a_way_back(self):
        later = time.time() + MAX_AGE + 60
        with patch("django.core.signing.time.time", return_value=later):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "expired", status_code=400)

    def test_a_link_inside_three_days_still_works(self):
        with patch("django.core.signing.time.time", return_value=time.time() + MAX_AGE - 60):
            self.assertEqual(self.client.get(self.path).status_code, 200)

    def test_a_tampered_link_is_refused(self):
        response = self.client.get(reverse("console:invitation_accept", args=["1:abc:nonsense"]))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, escape("doesn't look right"), status_code=400)

    def test_resending_gives_a_fresh_working_link(self):
        self.client.force_login(self.owner)
        mail.outbox.clear()
        response = self.client.post(reverse("console:team_resend_invite", args=[self.invited.pk]), follow=True)
        self.assertContains(response, "New invitation sent to dana@inkedgraphics.com")

        new_link = re.search(r"https?://[^\s]+/console/invite/[^\s]+", mail.outbox[0].body).group(0)
        self.client.logout()
        self.accept(path=new_link.split("testserver", 1)[1])
        self.invited.refresh_from_db()
        self.assertTrue(self.invited.is_active)

    def test_resending_to_someone_already_set_up_is_refused(self):
        self.accept()
        self.client.logout()
        self.client.force_login(self.owner)
        response = self.client.post(reverse("console:team_resend_invite", args=[self.invited.pk]), follow=True)
        self.assertContains(response, "has already set up their account")

    def test_an_invited_account_cannot_be_signed_into_yet(self):
        response = self.client.post(
            reverse("console:login"), {"username": "dana@inkedgraphics.com", "password": "chosen-pass-4417"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
