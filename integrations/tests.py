from decimal import Decimal
from io import StringIO
from unittest.mock import Mock

import requests
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from catalog.models import Blank, BlankImage, BlankVariant

from .catalog_sync import sync_catalog
from .fixtures.ops_catalog import CC1567, FakeOpsCatalog, style
from .models import CatalogSyncState
from .ops_client import OpsClient, OpsPermanentError, OpsTemporaryError


def response(status=200, json_body=None, text=""):
    fake = Mock(spec=["status_code", "json", "text"])
    fake.status_code = status
    fake.text = text
    fake.json = Mock(return_value=json_body) if json_body is not None else Mock(side_effect=ValueError("no json"))
    return fake


@override_settings(OPS_API_URL="https://ops.test", OPS_API_TOKEN="svc_test")
class OpsClientTests(SimpleTestCase):
    """The client's only job is fetching pages and sorting failures into retry / don't retry."""

    def client_with(self, *responses):
        session = Mock()
        session.get = Mock(side_effect=list(responses))
        return OpsClient(session=session), session

    def test_it_sends_the_contract_auth_header_and_timeouts(self):
        client, session = self.client_with(response(200, {"ok": True}))
        client.get("/api/v1/catalog/styles/")
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Token svc_test")
        self.assertEqual(kwargs["timeout"], (5, 30))

    def test_a_next_url_is_fetched_as_given(self):
        client, session = self.client_with(response(200, {"ok": True}))
        client.get("https://ops.test/api/v1/catalog/styles/?page=2")
        self.assertEqual(session.get.call_args.args[0], "https://ops.test/api/v1/catalog/styles/?page=2")

    def test_server_errors_and_rate_limits_are_worth_retrying(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                client, _ = self.client_with(response(status, {"error": {"code": "server_error", "message": "boom"}}))
                with self.assertLogs("integrations.ops_client", "WARNING"), self.assertRaises(OpsTemporaryError):
                    client.get("/api/v1/catalog/styles/")

    def test_client_errors_are_not_worth_retrying(self):
        for status, code in [(400, "invalid"), (401, "authentication_failed"), (403, "permission_denied"), (404, "not_found")]:
            with self.subTest(status=status):
                client, _ = self.client_with(response(status, {"error": {"code": code, "message": "nope"}}))
                with self.assertLogs("integrations.ops_client", "WARNING"), self.assertRaises(OpsPermanentError) as caught:
                    client.get("/api/v1/catalog/styles/")
                self.assertEqual((caught.exception.status, caught.exception.code), (status, code))

    def test_an_html_error_page_from_the_proxy_is_still_classified(self):
        # The ALB serves its own HTML when the target is down; parsing it must not mask the 502.
        client, _ = self.client_with(response(502, text="<html><body>502 Bad Gateway</body></html>"))
        with self.assertLogs("integrations.ops_client", "WARNING"), self.assertRaises(OpsTemporaryError) as caught:
            client.get("/api/v1/catalog/styles/")
        self.assertEqual(caught.exception.status, 502)
        self.assertIn("502", str(caught.exception))

    def test_a_200_that_is_not_json_is_treated_as_temporary(self):
        client, _ = self.client_with(response(200, text="<html>maintenance</html>"))
        with self.assertRaises(OpsTemporaryError):
            client.get("/api/v1/catalog/styles/")

    def test_a_connection_failure_is_temporary(self):
        session = Mock()
        session.get = Mock(side_effect=requests.ConnectionError("refused"))
        with self.assertRaises(OpsTemporaryError):
            OpsClient(session=session).get("/api/v1/catalog/styles/")

    def test_a_read_timeout_is_temporary(self):
        # The ALB cuts a response at its idle timeout; that must look retryable, not fatal.
        session = Mock()
        session.get = Mock(side_effect=requests.ReadTimeout("too slow"))
        with self.assertRaises(OpsTemporaryError):
            OpsClient(session=session).get("/api/v1/catalog/styles/")

    @override_settings(OPS_API_URL="", OPS_API_TOKEN="")
    def test_an_unconfigured_client_says_so_rather_than_calling_nowhere(self):
        client = OpsClient(session=Mock())
        self.assertFalse(client.is_configured)
        with self.assertRaises(OpsPermanentError):
            client.get("/api/v1/catalog/styles/")


class CatalogSyncTests(TestCase):
    def test_the_contract_payload_maps_field_for_field(self):
        sync_catalog(client=FakeOpsCatalog())

        blank = Blank.objects.get(style_id=407)
        self.assertEqual(blank.supplier_style_code, "CC1567")
        self.assertEqual(blank.brand, "Comfort Colors")
        self.assertEqual(blank.display_title, CC1567["display_title"])
        self.assertEqual(blank.audience, "adult")
        self.assertEqual(blank.stock_policy, "job_only")
        self.assertEqual(blank.base_cost, Decimal("25.92"))
        self.assertEqual(blank.currency, "USD")
        self.assertIsNone(blank.size_chart)
        self.assertEqual(blank.ops_updated_at.isoformat(), "2026-09-22T20:37:09+00:00")

        variant = blank.variants.get()
        self.assertEqual(variant.blank_sku, "CC1567-BlueJean-S")
        self.assertEqual((variant.color_name, variant.color_hex, variant.size), ("BlueJean", "#596B83", "S"))
        self.assertEqual(variant.size_sort_order, 2)
        self.assertEqual(variant.cost_adjustment, Decimal("0.00"))
        self.assertFalse(variant.is_active)

        image = blank.images.get()
        self.assertEqual(image.image_type, "large")
        self.assertEqual(image.view, "")  # null in v1

    def test_nulls_become_blanks_rather_than_the_word_none(self):
        sync_catalog(client=FakeOpsCatalog())
        blank = Blank.objects.get(style_id=407)
        self.assertEqual((blank.merch_label, blank.category), ("", ""))

    def test_a_buyer_never_sees_supplier_copy(self):
        sync_catalog(client=FakeOpsCatalog())
        blank = Blank.objects.get(style_id=407)
        self.assertEqual(blank.buyer_name, "Comfort Colors CC1567")
        self.assertNotIn("Ringspun", blank.buyer_name)

        blank.merch_label = "Terry Hoodie"
        self.assertEqual(blank.buyer_name, "Terry Hoodie")

    def test_running_it_twice_changes_nothing(self):
        # The contract's stated verification for the storefront side.
        fake = FakeOpsCatalog(styles=[style(1), style(2), style(3)])
        first = sync_catalog(client=fake)
        before = list(Blank.objects.values_list("style_id", "supplier_style_code", "is_active"))

        second = sync_catalog(client=fake)
        self.assertEqual(first.styles, 3)
        self.assertEqual(second.created, 0)
        self.assertEqual(list(Blank.objects.values_list("style_id", "supplier_style_code", "is_active")), before)
        self.assertEqual(Blank.objects.count(), 3)
        self.assertEqual(BlankVariant.objects.count(), 3)

    def test_every_page_is_followed(self):
        fake = FakeOpsCatalog(styles=[style(n) for n in range(1, 6)], page_size=2)
        result = sync_catalog(client=fake)
        self.assertEqual(result.styles, 5)
        self.assertEqual([r["page"] for r in fake.requests], [1, 2, 3])

    def test_a_style_repeated_across_a_page_boundary_is_harmless(self):
        # §4: "A page boundary can repeat a style but never skip one."
        fake = FakeOpsCatalog(styles=[style(1), style(2), style(1)], page_size=2)
        sync_catalog(client=fake)
        self.assertEqual(Blank.objects.count(), 2)

    def test_archived_styles_are_marked_inactive_not_deleted(self):
        fake = FakeOpsCatalog(styles=[style(1)])
        sync_catalog(client=fake)
        self.assertTrue(Blank.objects.get(style_id=1).is_active)

        fake.catalog = [style(1, is_active=False)]
        result = sync_catalog(client=fake)
        blank = Blank.objects.get(style_id=1)
        self.assertFalse(blank.is_active)
        self.assertIsNotNone(blank.deactivated_at)
        self.assertEqual(Blank.objects.count(), 1)
        self.assertEqual([str(r) for r in result.deactivated], [str(blank)])

    def test_a_style_coming_back_clears_the_deactivation(self):
        fake = FakeOpsCatalog(styles=[style(1, is_active=False)])
        sync_catalog(client=fake)
        fake.catalog = [style(1, is_active=True)]
        sync_catalog(client=fake)
        blank = Blank.objects.get(style_id=1)
        self.assertTrue(blank.is_active)
        self.assertIsNone(blank.deactivated_at)

    def test_images_are_replaced_rather_than_piling_up(self):
        fake = FakeOpsCatalog(styles=[style(1)])
        sync_catalog(client=fake)
        sync_catalog(client=fake)
        self.assertEqual(BlankImage.objects.count(), 1)

    def test_a_style_with_no_images_is_fine(self):
        sync_catalog(client=FakeOpsCatalog(styles=[style(1, images=[])]))
        self.assertEqual(BlankImage.objects.count(), 0)

    def test_a_variant_with_no_price_syncs_anyway(self):
        payload = style(1, base_cost=None, variants=[{**CC1567["variants"][0], "cost_adjustment": None}])
        sync_catalog(client=FakeOpsCatalog(styles=[payload]))
        self.assertIsNone(Blank.objects.get(style_id=1).base_cost)
        self.assertIsNone(BlankVariant.objects.get().cost_adjustment)


class SyncCursorTests(TestCase):
    def test_the_first_run_is_a_full_sync_and_stores_the_servers_time(self):
        fake = FakeOpsCatalog(styles=[style(1)], server_time="2026-09-22T22:32:33Z")
        result = sync_catalog(client=fake)
        self.assertTrue(result.full)
        self.assertIsNone(fake.requests[0]["updated_since"])
        self.assertEqual(CatalogSyncState.load().cursor, "2026-09-22T22:32:33Z")

    def test_the_next_run_sends_the_stored_cursor_back_unchanged(self):
        fake = FakeOpsCatalog(styles=[style(1)], server_time="2026-09-22T22:32:33Z")
        sync_catalog(client=fake)
        fake.requests.clear()

        result = sync_catalog(client=fake)
        self.assertFalse(result.full)
        self.assertEqual(fake.requests[0]["updated_since"], "2026-09-22T22:32:33Z")

    def test_the_cursor_comes_from_the_first_page_not_the_last(self):
        # A style changed mid-run must be caught next time, not skipped.
        fake = FakeOpsCatalog(styles=[style(n) for n in range(1, 6)], page_size=2)
        sync_catalog(client=fake)
        self.assertEqual(CatalogSyncState.load().cursor, fake.server_time)

    def test_a_failure_part_way_through_leaves_the_old_cursor(self):
        fake = FakeOpsCatalog(styles=[style(n) for n in range(1, 6)], page_size=2, server_time="FIRST")
        sync_catalog(client=fake)

        fake.server_time = "SECOND"
        fake.catalog = [style(n) for n in range(1, 8)]
        fake.break_on(2)
        with self.assertRaises(OpsTemporaryError):
            sync_catalog(client=fake)

        self.assertEqual(CatalogSyncState.load().cursor, "FIRST")

    def test_full_ignores_the_cursor(self):
        fake = FakeOpsCatalog(styles=[style(1)])
        sync_catalog(client=fake)
        fake.requests.clear()

        result = sync_catalog(client=fake, full=True)
        self.assertTrue(result.full)
        self.assertIsNone(fake.requests[0]["updated_since"])
        self.assertIsNotNone(CatalogSyncState.load().last_full_sync_at)


class SyncCatalogCommandTests(TestCase):
    def run_command(self, **options):
        out, err = StringIO(), StringIO()
        try:
            call_command("sync_catalog", stdout=out, stderr=err, **options)
        except SystemExit as exc:
            return out.getvalue(), err.getvalue(), exc.code
        return out.getvalue(), err.getvalue(), 0

    @override_settings(OPS_API_URL="", OPS_API_TOKEN="")
    def test_it_skips_quietly_when_there_is_no_ops_link(self):
        out, _, code = self.run_command()
        self.assertIn("skipped", out)
        self.assertEqual(code, 0)

    @override_settings(OPS_API_URL="https://ops.test", OPS_API_TOKEN="svc_test")
    def test_a_failure_exits_non_zero_and_records_why(self):
        from unittest.mock import patch

        with patch("integrations.management.commands.sync_catalog.sync_catalog",
                   side_effect=OpsTemporaryError("ops fell over", status=503)):
            out, err, code = self.run_command()
        self.assertEqual(code, 1)
        self.assertIn("ops fell over", err)
        self.assertIn("ops fell over", CatalogSyncState.load().last_error)
