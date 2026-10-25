from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.agent.policy_engine import (
    IST,
    OrderForPolicy,
    check_return_eligibility,
    refund_within_limits,
    within_office_hours,
)

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=IST)


@dataclass(frozen=True)
class Scenario:
    name: str
    check: Callable[[], bool]


def _ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def _elig(
    delivered_at: datetime | None,
    window: int = 7,
    tags: tuple[str, ...] = (),
    final_sale: tuple[str, ...] = (),
    financial: str = "paid",
    fulfillment: str = "fulfilled",
    now: datetime = NOW,
) -> tuple[str, str]:
    result = check_return_eligibility(
        OrderForPolicy(
            delivered_at=delivered_at,
            tags=tags,
            financial_status=financial,
            fulfillment_status=fulfillment,
        ),
        now,
        window,
        list(final_sale),
    )
    return result.decision, result.reason


def _eligible(*args: object, **kwargs: object) -> Callable[[], bool]:
    return lambda: _elig(*args, **kwargs) == ("eligible", "WITHIN_WINDOW")  # type: ignore[arg-type]


def _expect(expected: tuple[str, str], *args: object, **kwargs: object) -> Callable[[], bool]:
    return lambda: _elig(*args, **kwargs) == expected  # type: ignore[arg-type]


def _refund(amount: int, today: int, auto: int | None, daily: int | None) -> bool:
    return refund_within_limits(amount, today, auto, daily)


def _hours(moment: datetime, start: str, end: str, days: list[int]) -> bool:
    return within_office_hours(moment, start, end, days)


ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
WEEKDAYS = [0, 1, 2, 3, 4]
TUESDAY_10_AM = datetime(2026, 3, 10, 10, 0, tzinfo=IST)
SATURDAY = datetime(2026, 3, 14, 11, 0, tzinfo=IST)

SCENARIOS: list[Scenario] = [
    Scenario("window7_delivered_6_days_ago_eligible", _eligible(_ago(6))),
    Scenario("window7_delivered_7_days_ago_eligible", _eligible(_ago(7))),
    Scenario(
        "window7_delivered_8_days_ago_expired", _expect(("not_eligible", "WINDOW_EXPIRED"), _ago(8))
    ),
    Scenario("window7_delivered_today_eligible", _eligible(_ago(0))),
    Scenario("window1_delivered_1_day_ago_eligible", _eligible(_ago(1), window=1)),
    Scenario(
        "window1_delivered_2_days_ago_expired",
        _expect(("not_eligible", "WINDOW_EXPIRED"), _ago(2), window=1),
    ),
    Scenario("window30_delivered_30_days_ago_eligible", _eligible(_ago(30), window=30)),
    Scenario(
        "window30_delivered_31_days_ago_expired",
        _expect(("not_eligible", "WINDOW_EXPIRED"), _ago(31), window=30),
    ),
    Scenario("window60_delivered_60_days_ago_eligible", _eligible(_ago(60), window=60)),
    Scenario(
        "window7_delivered_400_days_ago_expired",
        _expect(("not_eligible", "WINDOW_EXPIRED"), _ago(400)),
    ),
    Scenario(
        "final_sale_tag_blocks_inside_window",
        _expect(
            ("not_eligible", "FINAL_SALE"), _ago(1), tags=("clearance",), final_sale=("clearance",)
        ),
    ),
    Scenario(
        "final_sale_tag_case_insensitive",
        _expect(
            ("not_eligible", "FINAL_SALE"), _ago(1), tags=("CLEARANCE",), final_sale=("clearance",)
        ),
    ),
    Scenario(
        "final_sale_tag_whitespace_normalised",
        _expect(
            ("not_eligible", "FINAL_SALE"),
            _ago(1),
            tags=("Final  Sale",),
            final_sale=("final sale",),
        ),
    ),
    Scenario(
        "final_sale_tag_one_of_many",
        _expect(
            ("not_eligible", "FINAL_SALE"),
            _ago(2),
            tags=("new", "sale-item"),
            final_sale=("sale-item", "clearance"),
        ),
    ),
    Scenario(
        "unrelated_tag_does_not_block", _eligible(_ago(2), tags=("new",), final_sale=("clearance",))
    ),
    Scenario("final_sale_list_empty_never_blocks", _eligible(_ago(2), tags=("clearance",))),
    Scenario(
        "final_sale_beats_expired_window",
        _expect(
            ("not_eligible", "FINAL_SALE"), _ago(30), tags=("clearance",), final_sale=("clearance",)
        ),
    ),
    Scenario(
        "refunded_order_not_eligible",
        _expect(("not_eligible", "ALREADY_REFUNDED"), _ago(1), financial="refunded"),
    ),
    Scenario(
        "voided_order_not_eligible",
        _expect(("not_eligible", "ALREADY_REFUNDED"), _ago(1), financial="voided"),
    ),
    Scenario(
        "refunded_beats_final_sale",
        _expect(
            ("not_eligible", "ALREADY_REFUNDED"),
            _ago(1),
            tags=("clearance",),
            final_sale=("clearance",),
            financial="refunded",
        ),
    ),
    Scenario(
        "unfulfilled_order_not_delivered",
        _expect(("not_eligible", "NOT_DELIVERED"), _ago(1), fulfillment="unfulfilled"),
    ),
    Scenario("partially_fulfilled_can_be_eligible", _eligible(_ago(1), fulfillment="partial")),
    Scenario(
        "restocked_order_not_delivered",
        _expect(("not_eligible", "NOT_DELIVERED"), _ago(1), fulfillment="restocked"),
    ),
    Scenario(
        "unknown_delivery_date_needs_human", _expect(("needs_human", "DELIVERY_DATE_UNKNOWN"), None)
    ),
    Scenario(
        "delivery_in_future_needs_human",
        _expect(("needs_human", "DELIVERY_IN_FUTURE"), NOW + timedelta(days=2)),
    ),
    Scenario(
        "ist_midnight_boundary_counts_one_day",
        _expect(
            ("eligible", "WITHIN_WINDOW"),
            datetime(2026, 3, 9, 23, 59, tzinfo=IST),
            window=1,
            now=datetime(2026, 3, 10, 0, 1, tzinfo=IST),
        ),
    ),
    Scenario(
        "utc_evening_counts_as_next_ist_day",
        _expect(
            ("eligible", "WITHIN_WINDOW"),
            datetime(2026, 3, 3, 20, 0, tzinfo=UTC),
            window=7,
            now=datetime(2026, 3, 11, 5, 0, tzinfo=UTC),
        ),
    ),
    Scenario(
        "utc_morning_same_ist_day_eligible",
        _expect(
            ("eligible", "WITHIN_WINDOW"),
            datetime(2026, 3, 3, 1, 0, tzinfo=UTC),
            window=7,
            now=datetime(2026, 3, 10, 5, 0, tzinfo=UTC),
        ),
    ),
    Scenario("refund_equal_to_limit_allowed", lambda: _refund(50000, 0, 50000, 100000)),
    Scenario("refund_one_paisa_over_limit_rejected", lambda: not _refund(50001, 0, 50000, 100000)),
    Scenario("refund_one_paisa_allowed", lambda: _refund(1, 0, 50000, 100000)),
    Scenario("refund_zero_rejected", lambda: not _refund(0, 0, 50000, 100000)),
    Scenario("refund_negative_rejected", lambda: not _refund(-100, 0, 50000, 100000)),
    Scenario("refund_off_when_auto_limit_unset", lambda: not _refund(100, 0, None, 100000)),
    Scenario("refund_off_when_daily_limit_unset", lambda: not _refund(100, 0, 50000, None)),
    Scenario("refund_off_when_both_unset", lambda: not _refund(100, 0, None, None)),
    Scenario(
        "refund_reaches_daily_cap_exactly_allowed", lambda: _refund(20000, 80000, 50000, 100000)
    ),
    Scenario(
        "refund_exceeds_daily_cap_by_one_paisa_rejected",
        lambda: not _refund(20001, 80000, 50000, 100000),
    ),
    Scenario(
        "refund_when_daily_cap_already_used_rejected", lambda: not _refund(1, 100000, 50000, 100000)
    ),
    Scenario(
        "refund_two_small_refunds_accumulate",
        lambda: _refund(30000, 30000, 50000, 100000) and not _refund(30000, 90000, 50000, 100000),
    ),
    Scenario(
        "refund_large_amounts_stay_integer",
        lambda: _refund(9_999_999_900, 0, 9_999_999_900, 9_999_999_900),
    ),
    Scenario(
        "office_hours_start_inclusive",
        lambda: _hours(datetime(2026, 3, 10, 9, 0, tzinfo=IST), "09:00", "21:00", ALL_DAYS),
    ),
    Scenario(
        "office_hours_one_minute_before_start_closed",
        lambda: not _hours(datetime(2026, 3, 10, 8, 59, tzinfo=IST), "09:00", "21:00", ALL_DAYS),
    ),
    Scenario(
        "office_hours_end_exclusive",
        lambda: not _hours(datetime(2026, 3, 10, 21, 0, tzinfo=IST), "09:00", "21:00", ALL_DAYS),
    ),
    Scenario(
        "office_hours_one_minute_before_end_open",
        lambda: _hours(datetime(2026, 3, 10, 20, 59, tzinfo=IST), "09:00", "21:00", ALL_DAYS),
    ),
    Scenario(
        "office_hours_weekday_open", lambda: _hours(TUESDAY_10_AM, "09:00", "21:00", WEEKDAYS)
    ),
    Scenario(
        "office_hours_weekend_closed", lambda: not _hours(SATURDAY, "09:00", "21:00", WEEKDAYS)
    ),
    Scenario(
        "office_hours_utc_input_converted_to_ist",
        lambda: _hours(datetime(2026, 3, 10, 4, 0, tzinfo=UTC), "09:00", "21:00", ALL_DAYS),
    ),
    Scenario(
        "office_hours_utc_late_night_closed",
        lambda: not _hours(datetime(2026, 3, 10, 18, 0, tzinfo=UTC), "09:00", "21:00", ALL_DAYS),
    ),
    Scenario(
        "office_hours_seconds_ignored",
        lambda: _hours(datetime(2026, 3, 10, 9, 0, 59, tzinfo=IST), "09:00", "21:00", ALL_DAYS),
    ),
    Scenario("naive_datetime_rejected", lambda: _raises_for_naive()),
]


def _raises_for_naive() -> bool:
    try:
        within_office_hours(datetime(2026, 3, 10, 10, 0), "09:00", "21:00", ALL_DAYS)
    except ValueError:
        return True
    return False


def run_all() -> list[str]:
    failed: list[str] = []
    for scenario in SCENARIOS:
        try:
            ok = scenario.check()
        except Exception:
            ok = False
        if not ok:
            failed.append(scenario.name)
    return failed
