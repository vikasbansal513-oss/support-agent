from datetime import date
from decimal import Decimal

import pytest

from app.billing import calc


def test_allocate_always_adds_up_to_total() -> None:
    parts = calc.allocate(10001, {"a": 2, "b": 1})
    assert parts == {"a": 6667, "b": 3334} and sum(parts.values()) == 10001
    for total in (0, 1, 2, 7, 99, 100, 12345):
        spread = calc.allocate(total, {"a": 1, "b": 1, "c": 1, "d": 0})
        assert sum(spread.values()) == total and spread["d"] == 0


def test_allocate_without_weight_gives_nothing() -> None:
    assert calc.allocate(500, {"a": 0, "b": 0}) == {"a": 0, "b": 0}
    assert calc.allocate(500, {}) == {}
    with pytest.raises(ValueError):
        calc.allocate(-1, {"a": 1})


def test_allocate_tie_goes_to_the_smaller_key_so_result_is_stable() -> None:
    assert calc.allocate(1, {"b": 1, "a": 1}) == {"a": 1, "b": 0}


def test_money_rounding_is_half_up_in_paise() -> None:
    assert calc.usd_to_paise(Decimal("0.01"), Decimal("88")) == 88
    assert calc.usd_to_paise(Decimal("6"), Decimal("88")) == 52800
    assert calc.round_paise(Decimal("0.005")) == 1
    assert calc.round_paise(Decimal("0.004")) == 0


def test_llm_cost_uses_per_million_rates() -> None:
    usd = calc.llm_cost_usd(1_000_000, 200_000, Decimal("3"), Decimal("15"))
    assert usd == Decimal("6")
    assert calc.llm_cost_usd(0, 0, Decimal("3"), Decimal("15")) == 0


def test_compute_cost_is_seconds_times_vcpu_and_memory_rate() -> None:
    usd = calc.compute_cost_usd(
        3_600_000, Decimal(1), Decimal("0.5"), Decimal("0.000024"), Decimal("0.0000025")
    )
    assert usd == Decimal("0.09090")
    assert calc.compute_cost_usd(0, Decimal(1), Decimal(1), Decimal(1), Decimal(1)) == 0


def test_gst_same_state_splits_into_cgst_and_sgst() -> None:
    split = calc.gst_split(553600, Decimal("18"), True, "06", "06")
    assert (split.kind, split.cgst_paise, split.sgst_paise, split.igst_paise) == (
        "intra",
        49824,
        49824,
        0,
    )
    assert split.total_paise == 99648


def test_gst_other_state_is_igst_and_missing_state_is_igst_too() -> None:
    split = calc.gst_split(553600, Decimal("18"), True, "06", "07")
    assert (split.kind, split.igst_paise, split.cgst_paise) == ("inter", 99648, 0)
    assert calc.gst_split(10000, Decimal("18"), True, "06", "").kind == "inter"
    assert calc.gst_split(10000, Decimal("18"), True, "", "").igst_paise == 1800


def test_gst_off_zero_rate_or_zero_amount_is_none() -> None:
    assert calc.gst_split(10000, Decimal("18"), False, "06", "06").kind == "none"
    assert calc.gst_split(10000, Decimal("0"), True, "06", "06").total_paise == 0
    assert calc.gst_split(0, Decimal("18"), True, "06", "06").kind == "none"


def test_gst_rounds_half_up_to_the_paisa() -> None:
    assert calc.gst_split(3, Decimal("18"), True, "06", "07").igst_paise == 1
    assert calc.gst_split(1001, Decimal("18"), True, "06", "07").igst_paise == 180


def test_period_parse_label_and_bounds() -> None:
    assert calc.parse_period("2026-09") == date(2026, 9, 1)
    assert calc.period_label(date(2026, 9, 1)) == "2026-09"
    for bad in ("2026-13", "2026-9", "26-09", "", "2026-00"):
        with pytest.raises(ValueError):
            calc.parse_period(bad)
    start, end = calc.period_bounds(date(2026, 12, 1))
    assert (start.year, start.month, end.year, end.month) == (2026, 12, 2027, 1)
    assert start.utcoffset() is not None and start.utcoffset().total_seconds() == 19800  # type: ignore[union-attr]


def test_fiscal_year_changes_on_first_april() -> None:
    assert calc.fiscal_year(date(2027, 3, 31)) == "2026-27"
    assert calc.fiscal_year(date(2027, 4, 1)) == "2027-28"
    assert calc.fiscal_year(date(2026, 10, 26)) == "2026-27"
    assert calc.fiscal_year(date(2099, 6, 1)) == "2099-00"


def test_invoice_number_stays_within_sixteen_characters() -> None:
    number = calc.format_invoice_no("INV", "2026-27", 1)
    assert number == "INV/2026-27/0001" and len(number) == 16
    assert len(calc.format_invoice_no("ABC", "2026-27", 9999)) == 16


def test_gstin_checks() -> None:
    assert calc.gstin_problem("", "06") is None
    assert calc.gstin_problem("06ABCDE1234F1Z5", "06") is None
    assert calc.gstin_problem("06ABCDE1234F1Z5", "") is None
    assert calc.gstin_problem("06ABCDE1234F1Z5", "07") is not None
    assert calc.gstin_problem("06ABCDE1234F1X5", "06") is not None
    assert calc.gstin_problem("25ABCDE1234F1Z5", "25") is not None
    assert "06" in calc.STATE_CODES and "25" not in calc.STATE_CODES and "99" in calc.STATE_CODES
