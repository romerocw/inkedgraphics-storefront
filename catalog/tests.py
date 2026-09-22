from django.test import TestCase

# Create your tests here.
from django.core.checks import run_checks
from django.test import SimpleTestCase, override_settings

from .checks import ops_api_is_configured


class OpsApiSettingsCheckTests(SimpleTestCase):
    """The ops link is two settings that are useless apart, so a half-set pair is called out."""

    def ids(self, **settings_kwargs):
        with override_settings(**settings_kwargs):
            return [warning.id for warning in ops_api_is_configured(None)]

    def test_no_ops_link_at_all_is_quiet(self):
        # The normal state until the catalog sync exists; it must not nag every deploy.
        self.assertEqual(self.ids(OPS_API_URL="", OPS_API_TOKEN=""), [])

    def test_a_complete_pair_is_quiet(self):
        self.assertEqual(self.ids(OPS_API_URL="https://ops.example.com", OPS_API_TOKEN="svc_x"), [])

    def test_a_url_without_a_token_warns(self):
        self.assertEqual(self.ids(OPS_API_URL="https://ops.example.com", OPS_API_TOKEN=""), ["catalog.W001"])

    def test_a_token_without_a_url_warns(self):
        self.assertEqual(self.ids(OPS_API_URL="", OPS_API_TOKEN="svc_x"), ["catalog.W002"])

    def test_a_plain_http_url_warns_about_the_token_in_clear_text(self):
        self.assertIn("catalog.W003", self.ids(OPS_API_URL="http://ops.example.com", OPS_API_TOKEN="svc_x"))

    def test_whitespace_is_not_mistaken_for_a_value(self):
        self.assertEqual(self.ids(OPS_API_URL="  ", OPS_API_TOKEN="  "), [])

    def test_the_check_is_registered_so_migrate_and_deploy_show_it(self):
        with override_settings(OPS_API_URL="https://ops.example.com", OPS_API_TOKEN=""):
            self.assertIn("catalog.W001", [w.id for w in run_checks(tags=["catalog"])])
