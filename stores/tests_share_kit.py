from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageDraw

from .factories import TempMediaMixin, make_owner, make_staff, make_store
from .models import Store
from .services import share_kit


class ShareKitBuildTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.store = make_store(name="Saxons Spirit", closes_at=timezone.now() + timedelta(days=10))

    def test_all_four_assets_are_built(self):
        self.assertTrue(share_kit.generate(self.store))
        self.store.refresh_from_db()
        for field_name in share_kit.ASSET_FIELDS:
            with self.subTest(field=field_name):
                field = getattr(self.store, field_name)
                self.assertTrue(field.name, f"{field_name} was not written")
                self.assertGreater(field.size, 0)

    def test_assets_land_under_their_own_store_prefix(self):
        share_kit.generate(self.store)
        self.store.refresh_from_db()
        self.assertTrue(self.store.share_qr_png.name.startswith(f"stores/{self.store.slug}/share/"))

    def test_another_stores_assets_are_kept_apart(self):
        other = make_store(name="Other Store", closes_at=timezone.now() + timedelta(days=10))
        share_kit.generate(self.store)
        share_kit.generate(other)
        self.store.refresh_from_db()
        other.refresh_from_db()
        self.assertNotEqual(self.store.share_qr_png.name, other.share_qr_png.name)
        self.assertNotIn(other.slug, self.store.share_qr_png.name)

    def test_the_social_image_is_the_size_the_platforms_want(self):
        image = Image.open(BytesIO(share_kit.social_png(self.store)))
        self.assertEqual(image.size, (share_kit.SOCIAL_SIZE, share_kit.SOCIAL_SIZE))

    def test_the_flyer_is_a_us_letter_pdf(self):
        data = share_kit.flyer_pdf(self.store)
        self.assertTrue(data.startswith(b"%PDF-"))

    def test_the_qr_encodes_the_store_url(self):
        # Re-encoding the expected URL must produce the very same image.
        self.assertEqual(share_kit.qr_png(self.store), share_kit.qr_png(self.store))
        other = make_store(name="Other", closes_at=self.store.closes_at)
        self.assertNotEqual(share_kit.qr_png(self.store), share_kit.qr_png(other))
        self.assertIn(self.store.slug, share_kit.store_url(self.store))

    def test_the_svg_qr_is_really_svg(self):
        self.assertIn(b"<svg", share_kit.qr_svg(self.store))

    def test_a_store_with_no_logo_still_builds(self):
        self.assertTrue(share_kit.generate(self.store))

    def test_a_long_name_is_shrunk_until_its_block_fits_the_box(self):
        # A name that wraps to many lines can fit the column and still run off the bottom,
        # which is exactly how it overlapped the URL the first time round.
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        name = "Langley High School Ladies Varsity Lacrosse Spring Spirit Wear Store 2027"
        font, lines = share_kit._fitted(name, share_kit.FONT_BOLD, 108, 900, 300, draw)
        self.assertLessEqual(len(lines) * font.size * share_kit.LINE_SPACING, 300)
        self.assertTrue(all(draw.textlength(line, font=font) <= 900 for line in lines))

    def test_a_very_long_name_still_produces_a_square_image(self):
        self.store.name = "Langley High School Ladies Varsity Lacrosse Spring Spirit Wear Store 2027"
        image = Image.open(BytesIO(share_kit.social_png(self.store)))
        self.assertEqual(image.size, (share_kit.SOCIAL_SIZE, share_kit.SOCIAL_SIZE))


class ShareKitFingerprintTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.store = make_store(name="Saxons Spirit", closes_at=timezone.now() + timedelta(days=10))
        share_kit.generate(self.store)
        self.store.refresh_from_db()

    def test_building_again_with_nothing_changed_does_nothing(self):
        self.assertFalse(share_kit.generate(self.store))

    def test_force_rebuilds_anyway(self):
        self.assertTrue(share_kit.generate(self.store, force=True))

    def test_a_new_close_date_rebuilds_the_kit(self):
        self.store.closes_at += timedelta(days=7)
        self.store.save(update_fields=["closes_at"])
        self.assertTrue(share_kit.generate(self.store))

    def test_a_new_name_rebuilds_the_kit(self):
        self.store.name = "Renamed Store"
        self.store.save(update_fields=["name"])
        self.assertTrue(share_kit.generate(self.store))

    def test_a_new_colour_rebuilds_the_kit(self):
        self.store.primary_color = "#8b0000"
        self.store.save(update_fields=["primary_color"])
        self.assertTrue(share_kit.generate(self.store))

    def test_an_unrelated_edit_rebuilds_nothing(self):
        self.store.subdomain = "saxons"
        self.store.save(update_fields=["subdomain"])
        self.assertFalse(share_kit.generate(self.store))

    def test_rebuilding_leaves_no_duplicate_files_behind(self):
        # Storage never overwrites, so a rebuild that forgot to delete would quietly leave
        # qr.png beside qr_Gt4E0ry.png and grow the prefix on every close-date change.
        share_dir = self.media_root / "stores" / self.store.slug / "share"
        self.assertEqual(len(list(share_dir.iterdir())), 4)
        for name in ("Renamed Store", "Renamed Again"):
            self.store.name = name
            self.store.save(update_fields=["name"])
            share_kit.generate(self.store)
        self.store.refresh_from_db()
        self.assertEqual(sorted(p.name for p in share_dir.iterdir()),
                         ["flyer.pdf", "qr.png", "qr.svg", "social.png"])


class ShareKitSweepTests(TempMediaMixin, TestCase):
    def test_only_open_stores_are_swept(self):
        opened = make_store(status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))
        closed = make_store(status=Store.Status.CLOSED, closes_at=timezone.now() - timedelta(days=10))
        draft = make_store(status=Store.Status.DRAFT, closes_at=timezone.now() + timedelta(days=10))

        built, failed = share_kit.refresh_open_stores()
        self.assertEqual((built, failed), (1, 0))
        for store in (opened, closed, draft):
            store.refresh_from_db()
        self.assertTrue(opened.has_share_kit)
        self.assertFalse(closed.has_share_kit)
        self.assertFalse(draft.has_share_kit)

    def test_a_second_sweep_builds_nothing(self):
        make_store(status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))
        share_kit.refresh_open_stores()
        self.assertEqual(share_kit.refresh_open_stores(), (0, 0))

    def test_one_broken_store_does_not_stop_the_others(self):
        make_store(name="Breaks", status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))
        good = make_store(name="Fine", status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))

        real = share_kit.flyer_pdf

        def explode(store):
            if store.name == "Breaks":
                raise OSError("no disk")
            return real(store)

        with patch.object(share_kit, "flyer_pdf", explode):
            with self.assertLogs("stores.services.share_kit", level="ERROR"):
                built, failed = share_kit.refresh_open_stores()

        self.assertEqual((built, failed), (1, 1))
        good.refresh_from_db()
        self.assertTrue(good.has_share_kit)

    def test_the_management_command_builds_one_store(self):
        store = make_store(status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))
        call_command("generate_share_kits", store=store.slug, stdout=StringIO())
        store.refresh_from_db()
        self.assertTrue(store.has_share_kit)

    def test_the_management_command_can_force_closed_stores(self):
        store = make_store(status=Store.Status.CLOSED, closes_at=timezone.now() - timedelta(days=3))
        call_command("generate_share_kits", force=True, stdout=StringIO())
        store.refresh_from_db()
        self.assertTrue(store.has_share_kit)


class ShareImageAndCardTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.store = make_store(name="Saxons Spirit", closes_at=timezone.now() + timedelta(days=10))

    def url(self):
        return reverse("store_share_image", args=[self.store.slug])

    def test_the_card_image_is_served_at_a_stable_unsigned_url(self):
        share_kit.generate(self.store)
        self.store.refresh_from_db()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertNotIn("?", self.url())
        self.assertIn("max-age", response["Cache-Control"])

    def test_a_store_without_a_kit_has_no_card_image(self):
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_the_store_page_advertises_the_card(self):
        share_kit.generate(self.store)
        page = self.client.get(reverse("store_detail", args=[self.store.slug]))
        self.assertContains(page, 'property="og:title"')
        self.assertContains(page, f'content="{self.store.name}"')
        self.assertContains(page, self.url())
        self.assertContains(page, "summary_large_image")

    def test_a_store_without_a_kit_still_has_usable_tags(self):
        page = self.client.get(reverse("store_detail", args=[self.store.slug]))
        self.assertContains(page, 'property="og:title"')
        self.assertNotContains(page, "summary_large_image")


class ShareKitConsoleTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.store = make_store(status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))
        self.client.force_login(make_staff())

    def tab(self):
        return self.client.get(reverse("console:store_detail", args=[self.store.pk]), {"tab": "share"})

    def test_the_tab_appears_once_a_store_is_open(self):
        self.assertContains(self.client.get(reverse("console:store_detail", args=[self.store.pk])), "Share kit")

        draft = make_store(status=Store.Status.DRAFT)
        self.assertNotContains(self.client.get(reverse("console:store_detail", args=[draft.pk])), "Share kit")

    def test_the_button_builds_the_kit(self):
        response = self.client.post(reverse("console:store_share_kit", args=[self.store.pk]))
        self.assertEqual(response.status_code, 302)
        self.store.refresh_from_db()
        self.assertTrue(self.store.has_share_kit)

    def test_the_tab_shows_the_assets_and_the_text_to_send(self):
        share_kit.generate(self.store)
        page = self.tab()
        self.assertContains(page, "Download PDF")
        self.assertContains(page, share_kit.store_url(self.store))
        self.assertContains(page, "is open!")

    def test_a_changed_store_is_flagged_as_stale(self):
        share_kit.generate(self.store)
        self.store.name = "Renamed"
        self.store.save(update_fields=["name"])
        self.assertContains(self.tab(), "has changed since this kit was built")

    def test_an_unbuilt_store_says_so(self):
        self.assertContains(self.tab(), "Nothing built yet")


class ShareKitFailureTests(TempMediaMixin, TestCase):
    """A store whose kit won't build has to be findable, not just counted in a log line."""

    def setUp(self):
        super().setUp()
        self.store = make_store(status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))

    def break_it(self):
        return patch.object(share_kit, "flyer_pdf", side_effect=OSError("no disk"))

    def test_the_reason_is_written_onto_the_store(self):
        with self.break_it(), self.assertLogs("stores.services.share_kit", level="ERROR"):
            built, error = share_kit.try_generate(self.store)
        self.assertFalse(built)
        self.store.refresh_from_db()
        self.assertIn("no disk", self.store.share_kit_error)
        self.assertFalse(self.store.has_share_kit)

    def test_a_failure_is_retried_rather_than_fingerprinted_away(self):
        with self.break_it(), self.assertLogs("stores.services.share_kit", level="ERROR"):
            share_kit.try_generate(self.store)
        self.store.refresh_from_db()
        # Nothing was recorded as built, so the next sweep tries again by itself.
        self.assertEqual(self.store.share_kit_fingerprint, "")
        self.assertEqual(share_kit.refresh_open_stores(), (1, 0))

    def test_a_later_success_clears_the_error(self):
        with self.break_it(), self.assertLogs("stores.services.share_kit", level="ERROR"):
            share_kit.try_generate(self.store)
        share_kit.try_generate(self.store)
        self.store.refresh_from_db()
        self.assertEqual(self.store.share_kit_error, "")
        self.assertTrue(self.store.has_share_kit)

    def test_a_half_built_store_is_not_saved(self):
        with self.break_it(), self.assertLogs("stores.services.share_kit", level="ERROR"):
            share_kit.try_generate(self.store)
        self.store.refresh_from_db()
        for field_name in share_kit.ASSET_FIELDS:
            with self.subTest(field=field_name):
                self.assertFalse(getattr(self.store, field_name).name)


class ShareKitDashboardTests(TempMediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_owner())

    def failing_store(self, name="Broken Store"):
        store = make_store(name=name, status=Store.Status.OPEN, closes_at=timezone.now() + timedelta(days=10))
        with patch.object(share_kit, "flyer_pdf", side_effect=OSError("no disk")):
            with self.assertLogs("stores.services.share_kit", level="ERROR"):
                share_kit.try_generate(store)
        return store

    def dashboard(self):
        return self.client.get(reverse("console:dashboard"))

    def test_a_healthy_system_says_so(self):
        self.assertContains(self.dashboard(), "Building normally")

    def test_a_failing_store_is_named_and_linked(self):
        store = self.failing_store()
        page = self.dashboard()
        self.assertContains(page, "Broken Store")
        self.assertContains(page, f"{reverse('console:store_detail', args=[store.pk])}?tab=share")
        self.assertNotContains(page, "Building normally")

    def test_a_closed_store_is_not_chased(self):
        store = self.failing_store()
        store.status = Store.Status.CLOSED
        store.save(update_fields=["status"])
        self.assertContains(self.dashboard(), "Building normally")

    def test_only_the_first_few_are_named(self):
        for n in range(4):
            self.failing_store(name=f"Broken {n}")
        page = self.dashboard()
        self.assertContains(page, "4 open stores can't build one")
        self.assertContains(page, "and more")

    def test_staff_without_team_access_do_not_see_the_system_box(self):
        self.client.force_login(make_staff())
        self.failing_store()
        self.assertNotContains(self.dashboard(), "Share kits")

    def test_the_share_tab_shows_the_reason(self):
        store = self.failing_store()
        page = self.client.get(reverse("console:store_detail", args=[store.pk]), {"tab": "share"})
        self.assertContains(page, "The last build failed")
        self.assertContains(page, "no disk")
