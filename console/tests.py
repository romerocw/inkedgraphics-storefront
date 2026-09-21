from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class PasswordChangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("console:password_change")
        cls.user = get_user_model().objects.create_user("staffer", password="old-pass-9271", is_staff=True)

    def test_anonymous_redirected_to_login(self):
        self.assertRedirects(self.client.get(self.url), f"{reverse('console:login')}?next={self.url}")

    def test_non_staff_forbidden(self):
        get_user_model().objects.create_user("buyer", password="old-pass-9271")
        self.client.login(username="buyer", password="old-pass-9271")
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_page_renders_for_staff(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, "Change password")

    def test_password_changed_and_session_kept(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"old_password": "old-pass-9271", "new_password1": "new-pass-4823", "new_password2": "new-pass-4823"},
        )
        self.assertRedirects(response, reverse("console:dashboard"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-pass-4823"))
        self.assertEqual(self.client.get(reverse("console:dashboard")).status_code, 200)

    def test_wrong_old_password_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"old_password": "wrong-pass", "new_password1": "new-pass-4823", "new_password2": "new-pass-4823"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("old_password", response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-pass-9271"))
