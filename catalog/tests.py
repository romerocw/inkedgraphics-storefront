from django.test import TestCase

# Create your tests here.
from decimal import Decimal

from django.core.checks import run_checks
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from stores.factories import make_blank, make_blank_offering, make_offering, make_product, make_store

from .models import StoreProduct

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


class StoreProductSourceTests(TestCase):
    """A store product is sold from a synced blank; the legacy catalog is on its way out."""

    def setUp(self):
        self.store = make_store()

    def test_exactly_one_catalog_backs_an_offering(self):
        blank = make_blank()
        product = make_product()
        for kwargs in ({}, {"blank": blank, "product": product}):
            with self.subTest(kwargs=sorted(kwargs)), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    StoreProduct.objects.create(store=self.store, price=Decimal("40.00"), **kwargs)

    def test_a_blank_backed_offering_names_itself_without_supplier_copy(self):
        blank = make_blank(merch_label="Terry Hoodie")
        offering = make_blank_offering(self.store, blank)
        self.assertEqual(offering.name, "Terry Hoodie")
        self.assertNotIn("SUPPLIER COPY", offering.name)

    def test_the_stores_own_name_still_wins(self):
        offering = make_blank_offering(self.store, make_blank(merch_label="Terry Hoodie"))
        offering.display_name = "Langley Lacrosse Hoodie"
        self.assertEqual(offering.name, "Langley Lacrosse Hoodie")

    def test_a_blank_with_no_merch_label_falls_back_to_brand_and_code(self):
        offering = make_blank_offering(self.store, make_blank(supplier_style_code="CC1567"))
        self.assertEqual(offering.name, "Comfort Colors CC1567")

    def test_variants_come_from_whichever_catalog_backs_it(self):
        blank_offering = make_blank_offering(self.store, make_blank(sizes=("M", "L")))
        self.assertEqual(sorted(v.size for v in blank_offering.variants()), ["L", "M"])

        legacy = make_offering(make_store(), make_product(variants=(("Black", "M"),)))
        self.assertEqual([v.size for v in legacy.variants()], ["M"])

    def test_archived_blank_variants_are_not_offered(self):
        blank = make_blank(sizes=("M", "2XL"))
        blank.variants.filter(size="2XL").update(is_active=False)
        offering = make_blank_offering(self.store, blank)
        self.assertEqual([v.size for v in offering.variants()], ["M"])


class SizeUpchargeTests(TestCase):
    """What a buyer pays extra for a big size is ours, not the ops cost adjustment."""

    def setUp(self):
        self.offering = make_blank_offering(make_store(), make_blank(sizes=("M", "2XL")), price="40.00")

    def test_the_site_default_applies_when_a_store_says_nothing(self):
        self.assertEqual(self.offering.upcharge_for("M"), Decimal("0"))
        self.assertEqual(self.offering.upcharge_for("2XL"), Decimal("2.00"))

    def test_a_store_product_can_override_the_default(self):
        self.offering.size_upcharges = {"2XL": "5.00"}
        self.assertEqual(self.offering.upcharge_for("2XL"), Decimal("5.00"))
        # An override replaces the table rather than merging, so an unlisted size is free.
        self.assertEqual(self.offering.upcharge_for("3XL"), Decimal("0"))

    def test_sizes_match_whatever_case_ops_sent(self):
        self.assertEqual(self.offering.upcharge_for("2xl"), Decimal("2.00"))

    def test_the_price_a_buyer_pays_adds_the_surcharge(self):
        medium, big = self.offering.variants().get(size="M"), self.offering.variants().get(size="2XL")
        self.assertEqual(self.offering.price_for(medium), Decimal("40.00"))
        self.assertEqual(self.offering.price_for(big), Decimal("42.00"))

    def test_the_surcharge_is_not_taken_from_what_the_blank_costs_us(self):
        # cost_adjustment is what ops pays extra; it must never leak into retail pricing.
        self.offering.blank.variants.filter(size="2XL").update(cost_adjustment=Decimal("11.00"))
        self.assertEqual(self.offering.upcharge_for("2XL"), Decimal("2.00"))

    def test_a_legacy_variant_keeps_the_upcharge_it_was_given(self):
        # Moving to the site's size table must not silently reprice a product nobody has
        # re-picked against a blank yet.
        legacy = make_offering(
            make_store(), make_product(variants=(("Black", "M"), ("Black", "2XL"))), price="40.00",
        )
        big = legacy.product.variants.get(size="2XL")
        big.upcharge = Decimal("7.00")
        big.save(update_fields=["upcharge"])
        self.assertEqual(legacy.price_for(big), Decimal("47.00"))
        self.assertEqual(legacy.price_for(legacy.product.variants.get(size="M")), Decimal("40.00"))
