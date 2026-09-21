from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orders.models import Order
from stores.factories import make_offering, make_order, make_product, make_staff, make_store


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


class StoreProductsTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.hoodie = make_product(name="Hoodie", sku_prefix="HOOD")
        self.tee = make_product(name="Tee", sku_prefix="TEE", default_price="20.00")
        self.retired = make_product(name="Retired Jacket", sku_prefix="JKT", is_active=False)
        self.client.force_login(make_staff())

    def products_url(self, tab="products"):
        return f"{reverse('console:store_detail', args=[self.store.pk])}?tab={tab}"

    def test_add_panel_offers_only_active_products_not_yet_in_the_store(self):
        make_offering(self.store, self.hoodie)
        response = self.client.get(self.products_url())
        self.assertEqual(list(response.context["add_form"].products), [self.tee])

    def test_add_selected_products_with_given_prices(self):
        response = self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]),
            {"add_selected": "1", f"add_{self.tee.pk}": "on", f"price_{self.tee.pk}": "25.50"},
            follow=True,
        )
        offering = self.store.offerings.get()
        self.assertEqual((offering.product, offering.price), (self.tee, Decimal("25.50")))
        self.assertTrue(offering.is_active)
        self.assertContains(response, "Added 1 product")

    def test_price_defaults_to_the_products_usual_price(self):
        self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]),
            {"add_selected": "1", f"add_{self.tee.pk}": "on", f"price_{self.tee.pk}": ""},
        )
        self.assertEqual(self.store.offerings.get().price, self.tee.default_price)

    def test_add_all_skips_products_already_in_the_store_and_inactive_ones(self):
        make_offering(self.store, self.hoodie, price="30.00")
        self.client.post(reverse("console:store_products_add", args=[self.store.pk]), {"add_all": "1"})
        self.assertEqual(
            sorted(self.store.offerings.values_list("product__name", flat=True)), ["Hoodie", "Tee"]
        )
        self.assertEqual(self.store.offerings.get(product=self.hoodie).price, Decimal("30.00"))

    def test_adding_nothing_tells_staff_what_to_do(self):
        response = self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]), {"add_selected": "1"}, follow=True
        )
        self.assertEqual(self.store.offerings.count(), 0)
        self.assertContains(response, "Tick the products you want to add")

    def test_a_bad_price_adds_nothing(self):
        response = self.client.post(
            reverse("console:store_products_add", args=[self.store.pk]),
            {"add_selected": "1", f"add_{self.tee.pk}": "on", f"price_{self.tee.pk}": "-5"},
        )
        self.assertEqual(self.store.offerings.count(), 0)
        self.assertContains(response, "Nothing was added")

    def test_inline_edits_save_price_active_and_order(self):
        offering = make_offering(self.store, self.hoodie, price="40.00")
        response = self.client.post(
            reverse("console:store_products", args=[self.store.pk]),
            {
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
                "form-0-id": str(offering.pk), "form-0-display_name": "Langley Hoodie",
                "form-0-price": "42.00", "form-0-sort_order": "3",
            },
            follow=True,
        )
        offering.refresh_from_db()
        self.assertEqual((offering.display_name, offering.price, offering.sort_order), ("Langley Hoodie", Decimal("42.00"), 3))
        self.assertFalse(offering.is_active)  # unchecked box means "not for sale"
        self.assertContains(response, "Saved 1 product")

    def test_a_bad_inline_edit_saves_nothing(self):
        offering = make_offering(self.store, self.hoodie, price="40.00")
        response = self.client.post(
            reverse("console:store_products", args=[self.store.pk]),
            {
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
                "form-0-id": str(offering.pk), "form-0-price": "not a price", "form-0-sort_order": "0",
            },
        )
        offering.refresh_from_db()
        self.assertEqual(offering.price, Decimal("40.00"))
        self.assertContains(response, "weren&#x27;t saved")

    def test_summary_shows_paid_orders_and_revenue(self):
        offering = make_offering(self.store, self.hoodie, price="40.00")
        make_order(self.store, items=[(offering, 2)])
        make_order(self.store, status=Order.Status.PENDING, items=[(offering, 1)])
        response = self.client.get(reverse("console:store_detail", args=[self.store.pk]))
        self.assertEqual(response.context["paid_orders"], 1)
        self.assertEqual(response.context["revenue"], Decimal("80.00"))
