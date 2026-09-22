from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from .arrival import ArrivalEstimate, add_business_days, estimated_arrival, federal_holidays, is_business_day
from .factories import make_store

PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")

# Defaults the tests count against, so a change to the real ones doesn't rewrite every date here.
ARRIVAL_DEFAULTS = {
    "DEFAULT_PRODUCTION_LEAD_DAYS": 10,
    "DEFAULT_SHIP_DAYS_ESTIMATE": 3,
    "DEFAULT_ARRIVAL_BUFFER_DAYS": 4,
    "BUSINESS_HOLIDAYS": [],
}


class HolidayTests(TestCase):
    def test_the_eleven_federal_holidays_for_a_year(self):
        self.assertEqual(
            sorted(day for day in federal_holidays(2026) if day.year == 2026),
            [
                date(2026, 1, 1),    # New Year's Day
                date(2026, 1, 19),   # MLK Day, 3rd Monday
                date(2026, 2, 16),   # Presidents' Day, 3rd Monday
                date(2026, 5, 25),   # Memorial Day, last Monday
                date(2026, 6, 19),   # Juneteenth
                date(2026, 7, 3),    # Independence Day, the 4th is a Saturday
                date(2026, 9, 7),    # Labor Day, 1st Monday
                date(2026, 10, 12),  # Columbus Day, 2nd Monday
                date(2026, 11, 11),  # Veterans Day
                date(2026, 11, 26),  # Thanksgiving, 4th Thursday
                date(2026, 12, 25),  # Christmas Day
            ],
        )

    def test_a_weekend_holiday_is_observed_on_the_nearest_weekday(self):
        # July 4th 2026 is a Saturday, observed the Friday before.
        self.assertFalse(is_business_day(date(2026, 7, 3)))
        self.assertTrue(is_business_day(date(2026, 7, 6)))
        # November 11th 2028 is a Saturday; 2029's is a Sunday, observed the Monday after.
        self.assertFalse(is_business_day(date(2028, 11, 10)))
        self.assertFalse(is_business_day(date(2029, 11, 12)))

    def test_new_years_day_on_a_saturday_is_observed_in_december(self):
        # January 1st 2028 is a Saturday, so December 31st 2027 is the day off.
        self.assertIn(date(2027, 12, 31), federal_holidays(2027))
        self.assertFalse(is_business_day(date(2027, 12, 31)))

    @override_settings(BUSINESS_HOLIDAYS=["2026-11-27"])
    def test_settings_add_shop_closures_to_the_federal_list(self):
        # The Friday after Thanksgiving isn't federal, but nothing is produced that day.
        self.assertFalse(is_business_day(date(2026, 11, 27)))
        self.assertEqual(add_business_days(date(2026, 11, 25), 1), date(2026, 11, 30))


class BusinessDayTests(TestCase):
    @override_settings(BUSINESS_HOLIDAYS=[])
    def test_counting_starts_the_day_after_and_skips_weekends(self):
        friday = date(2026, 10, 2)
        self.assertEqual(add_business_days(friday, 1), date(2026, 10, 5))
        self.assertEqual(add_business_days(friday, 5), date(2026, 10, 9))

    @override_settings(BUSINESS_HOLIDAYS=[])
    def test_counting_skips_holidays(self):
        # Columbus Day 2026 falls on Monday the 12th, so the 5th business day slides a day.
        self.assertEqual(add_business_days(date(2026, 10, 9), 1), date(2026, 10, 13))

    @override_settings(BUSINESS_HOLIDAYS=[])
    def test_zero_days_rolls_a_weekend_forward(self):
        self.assertEqual(add_business_days(date(2026, 10, 3), 0), date(2026, 10, 5))
        self.assertEqual(add_business_days(date(2026, 10, 5), 0), date(2026, 10, 5))


@override_settings(**ARRIVAL_DEFAULTS)
class EstimatedArrivalTests(TestCase):
    def store(self, closes, zone=EASTERN, **kwargs):
        return make_store(closes_at=datetime(*closes, tzinfo=zone), time_zone=str(zone), **kwargs)

    def test_a_store_closing_on_a_friday(self):
        # 13 business days from Friday Oct 2nd, skipping Columbus Day, then a 4-day range.
        store = self.store((2026, 10, 2, 18, 0))
        self.assertEqual(
            estimated_arrival(store, as_of=date(2026, 10, 2)),
            ArrivalEstimate(date(2026, 10, 22), date(2026, 10, 28)),
        )

    def test_the_promise_is_counted_from_the_close_date_not_from_today(self):
        store = self.store((2026, 10, 2, 18, 0))
        early = estimated_arrival(store, as_of=date(2026, 9, 1))
        late = estimated_arrival(store, as_of=date(2026, 10, 1))
        self.assertEqual(early, late)

    def test_a_closed_store_never_promises_a_date_in_the_past(self):
        store = self.store((2025, 1, 6, 18, 0))
        arrival = estimated_arrival(store, as_of=date(2026, 9, 22))
        self.assertEqual(arrival, ArrivalEstimate(date(2026, 9, 22), date(2026, 9, 28)))

    def test_the_close_date_is_read_on_the_stores_own_calendar(self):
        # 10pm Pacific on Monday the 5th is already Tuesday the 6th in Eastern. Production
        # counts from the store's Monday, so this lands a business day earlier than Eastern would.
        store = self.store((2026, 10, 5, 22, 0), zone=PACIFIC)
        arrival = estimated_arrival(store, as_of=date(2026, 10, 5))
        self.assertEqual(arrival.earliest, date(2026, 10, 23))

    def test_a_store_can_override_any_of_the_three_settings(self):
        store = self.store(
            (2026, 10, 2, 18, 0), production_lead_days=0, ship_days_estimate=1, arrival_buffer_days=0
        )
        arrival = estimated_arrival(store, as_of=date(2026, 10, 2))
        self.assertEqual(arrival, ArrivalEstimate(date(2026, 10, 5), date(2026, 10, 5)))

    def test_a_store_with_no_close_date_promises_nothing(self):
        self.assertIsNone(estimated_arrival(make_store(closes_at=None)))
        self.assertIsNone(make_store(closes_at=None).arrival)

    def test_the_store_property_matches_the_service(self):
        store = self.store((2026, 10, 2, 18, 0))
        self.assertEqual(store.arrival, estimated_arrival(store))


class ArrivalLabelTests(TestCase):
    def test_a_range_inside_one_month_names_the_month_once(self):
        self.assertEqual(ArrivalEstimate(date(2026, 11, 10), date(2026, 11, 14)).label, "November 10 – 14")

    def test_a_range_across_months_names_both(self):
        self.assertEqual(ArrivalEstimate(date(2026, 12, 31), date(2027, 1, 7)).label, "December 31 – January 7")

    def test_a_single_day_is_not_written_as_a_range(self):
        self.assertEqual(ArrivalEstimate(date(2026, 11, 10), date(2026, 11, 10)).label, "November 10")
