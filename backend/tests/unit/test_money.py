from decimal import Decimal

import pytest

from app.core.money import paise_from_decimal, paise_from_str


def test_rounding_half_up() -> None:
    assert paise_from_decimal(Decimal("123.45")) == 12345
    assert paise_from_decimal(Decimal("0.005")) == 1
    assert paise_from_decimal(Decimal("0.004")) == 0
    assert paise_from_decimal(Decimal("10")) == 1000
    assert paise_from_decimal(Decimal("1.005")) == 101


def test_float_and_non_finite_rejected() -> None:
    with pytest.raises(TypeError):
        paise_from_decimal(1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        paise_from_decimal(Decimal("NaN"))


def test_from_str() -> None:
    assert paise_from_str(" 499.00 ") == 49900
    with pytest.raises(ValueError):
        paise_from_str("abc")
