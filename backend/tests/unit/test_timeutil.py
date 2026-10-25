from datetime import UTC, datetime

import pytest

from app.core.timeutil import is_within_hours, next_open_utc, to_ist, utc_now

WEEKDAYS = [0, 1, 2, 3, 4]


def test_utc_now_is_aware_utc() -> None:
    assert utc_now().tzinfo is UTC


def test_to_ist_and_naive_rejected() -> None:
    assert to_ist(datetime(2026, 10, 24, 18, 30, tzinfo=UTC)).hour == 0
    with pytest.raises(ValueError):
        to_ist(datetime(2026, 10, 24))


def test_within_hours_boundaries() -> None:
    mon_10am_ist = datetime(2026, 10, 26, 4, 30, tzinfo=UTC)
    assert is_within_hours(mon_10am_ist, "10:00", "18:00", WEEKDAYS)
    mon_6pm_ist = datetime(2026, 10, 26, 12, 30, tzinfo=UTC)
    assert not is_within_hours(mon_6pm_ist, "10:00", "18:00", WEEKDAYS)
    sat = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)
    assert not is_within_hours(sat, "10:00", "18:00", WEEKDAYS)


def test_next_open_skips_weekend() -> None:
    sat = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)
    assert next_open_utc(sat, "10:00", "18:00", WEEKDAYS) == datetime(
        2026, 10, 26, 4, 30, tzinfo=UTC
    )


def test_next_open_when_already_open_and_when_never() -> None:
    open_now = datetime(2026, 10, 26, 5, 0, tzinfo=UTC)
    assert next_open_utc(open_now, "10:00", "18:00", WEEKDAYS) == open_now
    assert next_open_utc(open_now, "10:00", "18:00", []) is None


def test_next_open_later_same_day() -> None:
    early = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)
    assert next_open_utc(early, "10:00", "18:00", WEEKDAYS) == datetime(
        2026, 10, 26, 4, 30, tzinfo=UTC
    )
