from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_HUNDRED = Decimal(100)


def paise_from_decimal(rupees: Decimal) -> int:
    if not isinstance(rupees, Decimal):
        raise TypeError("rupees must be Decimal")
    if not rupees.is_finite():
        raise ValueError("rupees must be finite")
    return int((rupees * _HUNDRED).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paise_from_str(rupees: str) -> int:
    try:
        return paise_from_decimal(Decimal(rupees.strip()))
    except InvalidOperation as exc:
        raise ValueError("invalid money string") from exc
