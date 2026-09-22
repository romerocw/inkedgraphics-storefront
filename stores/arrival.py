"""The arrival promise: one computed date range, used everywhere a buyer asks when it arrives.

Production starts when the store closes, so the count runs from the close date rather than
from today. Everything is counted in business days: weekends, the eleven US federal holidays
and anything in ``settings.BUSINESS_HOLIDAYS`` are skipped.

Never render these dates through a store's time zone — they're plain dates, already worked out
on the store's own calendar by ``estimated_arrival``.
"""

import calendar
from datetime import date, timedelta
from functools import lru_cache
from typing import NamedTuple

from django.conf import settings
from django.utils import timezone

MONDAY, THURSDAY = 0, 3


class ArrivalEstimate(NamedTuple):
    """When a buyer should expect their order: the earliest and latest date we'll promise."""

    earliest: date
    latest: date

    @property
    def label(self):
        """The phrase buyers see, e.g. 'November 10 – 14'. Templates prefix it with 'Arrives'."""
        first = f"{self.earliest:%B} {self.earliest.day}"
        if self.earliest == self.latest:
            return first
        if (self.earliest.year, self.earliest.month) == (self.latest.year, self.latest.month):
            return f"{first} – {self.latest.day}"
        return f"{first} – {self.latest:%B} {self.latest.day}"

    def __str__(self):
        return self.label


def _nth_weekday(year, month, weekday, n):
    """The nth <weekday> of a month, e.g. the 3rd Monday of January. n = -1 for the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day):
    """A fixed-date federal holiday falling at a weekend is observed on the nearest weekday."""
    if day.weekday() == calendar.SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == calendar.SUNDAY:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=32)
def federal_holidays(year):
    """The eleven US federal holidays as observed in a year, as dates."""
    days = {
        _observed(date(year, 1, 1)),           # New Year's Day
        _nth_weekday(year, 1, MONDAY, 3),      # Martin Luther King Jr. Day
        _nth_weekday(year, 2, MONDAY, 3),      # Presidents' Day
        _nth_weekday(year, 5, MONDAY, -1),     # Memorial Day
        _observed(date(year, 6, 19)),          # Juneteenth
        _observed(date(year, 7, 4)),           # Independence Day
        _nth_weekday(year, 9, MONDAY, 1),      # Labor Day
        _nth_weekday(year, 10, MONDAY, 2),     # Columbus Day
        _observed(date(year, 11, 11)),         # Veterans Day
        _nth_weekday(year, 11, THURSDAY, 4),   # Thanksgiving
        _observed(date(year, 12, 25)),         # Christmas Day
    }
    # New Year's Day on a Saturday is observed on December 31st of the year before.
    next_new_year = _observed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        days.add(next_new_year)
    return days


def _extra_holidays():
    """Shop closures from settings, on top of the federal list. ISO strings or dates."""
    return {day if isinstance(day, date) else date.fromisoformat(day)
            for day in getattr(settings, "BUSINESS_HOLIDAYS", ()) or ()}


def is_business_day(day):
    """True if we'd be producing and shipping on this date."""
    return day.weekday() < calendar.SATURDAY and day not in federal_holidays(day.year) and day not in _extra_holidays()


def add_business_days(start, count):
    """The date ``count`` business days after ``start``.

    Counting begins the day after ``start``, so the close date itself is never counted as a
    production day. A count of zero rolls forward to the next business day, so a range never
    starts on a weekend or a holiday.
    """
    day = start
    for _ in range(count):
        day += timedelta(days=1)
        while not is_business_day(day):
            day += timedelta(days=1)
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def _days(override, default_setting):
    """A store's own setting, or the site default when the store leaves it blank."""
    return getattr(settings, default_setting) if override is None else override


def estimated_arrival(store, as_of=None):
    """When this store's buyers should expect their order, as an ArrivalEstimate.

    Returns None if the store has no close date — there's nothing to count production from.
    ``as_of`` (a date) stands in for today in tests.
    """
    if not store.closes_at:
        return None
    lead = _days(store.production_lead_days, "DEFAULT_PRODUCTION_LEAD_DAYS")
    ship = _days(store.ship_days_estimate, "DEFAULT_SHIP_DAYS_ESTIMATE")
    width = _days(store.arrival_buffer_days, "DEFAULT_ARRIVAL_BUFFER_DAYS")

    # The close date on the store's own calendar: a store closing at 9pm Pacific closes that
    # day, not the next one, however late that is in Eastern.
    earliest = add_business_days(store.closes_at.astimezone(store.zone).date(), lead + ship)

    # A promise in the past helps nobody, so a store that closed weeks ago promises from today.
    # TODO: once ops sends production status, a slipped batch's real date should override this.
    # An order placed before the slip keeps what its buyer was told, in Order.promised_arrival.
    today = as_of or timezone.localdate(timezone=store.zone)
    earliest = max(earliest, add_business_days(today, 0))
    return ArrivalEstimate(earliest, add_business_days(earliest, width))
