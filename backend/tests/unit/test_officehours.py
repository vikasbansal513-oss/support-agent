from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core import officehours, timeutil
from app.v1.policy import PolicyV2

WEEKDAYS = [0, 1, 2, 3, 4]


def _policy(**extra: object) -> PolicyV2:
    return PolicyV2.model_validate(
        {
            "schema_version": 2,
            "office_hours": {"start": "10:00", "end": "18:00", "days": WEEKDAYS},
            **extra,
        }
    )


MON_11_IST = datetime(2026, 10, 26, 5, 30, tzinfo=UTC)
SAT_11_IST = datetime(2026, 10, 24, 5, 30, tzinfo=UTC)
TZ = "Asia/Kolkata"


def test_defaults_match_old_rule_exactly() -> None:
    policy = _policy()
    for moment in (MON_11_IST, SAT_11_IST, datetime(2026, 10, 26, 12, 30, tzinfo=UTC)):
        assert officehours.is_open(policy, moment, TZ) == timeutil.is_within_hours(
            moment, "10:00", "18:00", WEEKDAYS, TZ
        )
        assert officehours.next_open(policy, moment, TZ) == timeutil.next_open_utc(
            moment, "10:00", "18:00", WEEKDAYS, TZ
        )


def test_holiday_closes_a_working_day_and_next_open_skips_it() -> None:
    policy = _policy(holidays=["2026-10-26"])
    assert not officehours.is_open(policy, MON_11_IST, TZ)
    assert officehours.next_open(policy, MON_11_IST, TZ) == datetime(
        2026, 10, 27, 4, 30, tzinfo=UTC
    )


def test_consecutive_holidays_and_weekend_are_skipped() -> None:
    policy = _policy(
        holidays=["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    )
    assert officehours.next_open(policy, MON_11_IST, TZ) == datetime(2026, 11, 2, 4, 30, tzinfo=UTC)


def test_day_hours_override_one_weekday_only() -> None:
    policy = _policy(day_hours={"0": {"start": "12:00", "end": "14:00"}})
    assert not officehours.is_open(policy, MON_11_IST, TZ)
    tue_11 = datetime(2026, 10, 27, 5, 30, tzinfo=UTC)
    assert officehours.is_open(policy, tue_11, TZ)
    assert officehours.next_open(policy, MON_11_IST, TZ) == datetime(
        2026, 10, 26, 6, 30, tzinfo=UTC
    )


def test_day_hours_cannot_open_a_closed_weekday() -> None:
    policy = _policy(day_hours={"5": {"start": "10:00", "end": "14:00"}})
    assert not officehours.is_open(policy, SAT_11_IST, TZ)


def test_policy_without_extras_object_still_works() -> None:
    class Old:
        office_hours = _policy().office_hours

    assert officehours.is_open(Old(), MON_11_IST, TZ)


def test_validation_rejects_bad_holiday_and_day_hours() -> None:
    with pytest.raises(ValidationError):
        _policy(holidays=["26-10-2026"])
    with pytest.raises(ValidationError):
        _policy(day_hours={"7": {"start": "10:00", "end": "11:00"}})
    with pytest.raises(ValidationError):
        _policy(day_hours={"0": {"start": "15:00", "end": "11:00"}})
    assert _policy(holidays=["2026-10-27", "2026-10-26", "2026-10-26"]).holidays == [
        "2026-10-26",
        "2026-10-27",
    ]
