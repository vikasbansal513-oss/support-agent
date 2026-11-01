import re
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal

from app.core.timeutil import IST

GstKind = Literal["none", "intra", "inter"]

STATE_CODES = frozenset({f"{n:02d}" for n in range(1, 39) if n not in (25, 28)} | {"97", "99"})
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PERIOD_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

_HUNDRED = Decimal(100)
_MILLION = Decimal(1_000_000)
_ONE = Decimal(1)


def parse_period(value: str) -> date:
    found = PERIOD_PATTERN.match(value)
    if found is None:
        raise ValueError("period must look like YYYY-MM")
    return date(int(found.group(1)), int(found.group(2)), 1)


def period_label(period_start: date) -> str:
    return f"{period_start.year:04d}-{period_start.month:02d}"


def period_bounds(period_start: date) -> tuple[datetime, datetime]:
    if period_start.month == 12:
        following = date(period_start.year + 1, 1, 1)
    else:
        following = date(period_start.year, period_start.month + 1, 1)
    return (
        datetime(period_start.year, period_start.month, 1, tzinfo=IST),
        datetime(following.year, following.month, 1, tzinfo=IST),
    )


def fiscal_year(on: date) -> str:
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def round_paise(rupees: Decimal) -> int:
    return int((rupees * _HUNDRED).quantize(_ONE, rounding=ROUND_HALF_UP))


def usd_to_paise(usd: Decimal, usd_inr: Decimal) -> int:
    return round_paise(usd * usd_inr)


def llm_cost_usd(
    input_tokens: int, output_tokens: int, input_per_million: Decimal, output_per_million: Decimal
) -> Decimal:
    return (
        Decimal(input_tokens) * input_per_million + Decimal(output_tokens) * output_per_million
    ) / _MILLION


def compute_cost_usd(
    milliseconds: int,
    vcpu: Decimal,
    gib: Decimal,
    usd_per_vcpu_second: Decimal,
    usd_per_gib_second: Decimal,
) -> Decimal:
    seconds = Decimal(milliseconds) / Decimal(1000)
    return seconds * (vcpu * usd_per_vcpu_second + gib * usd_per_gib_second)


def allocate[K: Hashable](total: int, weights: dict[K, int]) -> dict[K, int]:
    """Split total paise by weights so the parts always add up to exactly total."""
    if total < 0:
        raise ValueError("total must not be negative")
    positive = {k: w for k, w in weights.items() if w > 0}
    result: dict[K, int] = {k: 0 for k in weights}
    weight_sum = sum(positive.values())
    if total == 0 or weight_sum == 0:
        return result
    remainders: list[tuple[Decimal, str, K]] = []
    given = 0
    for key, weight in positive.items():
        exact = Decimal(total) * Decimal(weight) / Decimal(weight_sum)
        floor = int(exact.to_integral_value(rounding=ROUND_FLOOR))
        result[key] = floor
        given += floor
        remainders.append((exact - floor, str(key), key))
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _, _, key in remainders[: total - given]:
        result[key] += 1
    return result


@dataclass(frozen=True)
class GstSplit:
    kind: GstKind
    cgst_paise: int
    sgst_paise: int
    igst_paise: int

    @property
    def total_paise(self) -> int:
        return self.cgst_paise + self.sgst_paise + self.igst_paise


def gst_split(
    subtotal_paise: int,
    rate_percent: Decimal,
    enabled: bool,
    seller_state: str,
    buyer_state: str,
) -> GstSplit:
    if not enabled or rate_percent <= 0 or subtotal_paise <= 0:
        return GstSplit("none", 0, 0, 0)
    if seller_state and buyer_state and seller_state == buyer_state:
        half = round_paise(Decimal(subtotal_paise) / _HUNDRED * rate_percent / 2 / _HUNDRED)
        return GstSplit("intra", half, half, 0)
    igst = round_paise(Decimal(subtotal_paise) / _HUNDRED * rate_percent / _HUNDRED)
    return GstSplit("inter", 0, 0, igst)


def gstin_problem(gstin: str, state_code: str) -> str | None:
    if gstin == "":
        return None
    if GSTIN_PATTERN.match(gstin) is None:
        return "gstin format is wrong"
    if gstin[:2] not in STATE_CODES:
        return "gstin state code is unknown"
    if state_code and gstin[:2] != state_code:
        return "gstin does not match the state"
    return None


def format_invoice_no(prefix: str, fy: str, number: int) -> str:
    return f"{prefix}/{fy}/{number:04d}"


PaymentStatusV2 = Literal["draft", "paid", "partially_paid", "unpaid", "overdue", "cancelled"]


def payment_status(
    status: Literal["draft", "final"],
    due_date: date | None,
    paid_at: datetime | None,
    today: date,
    *,
    cancelled: bool = False,
    paid_amount_paise: int = 0,
    total_paise: int = 0,
) -> PaymentStatusV2:
    """Six-state payment status. Backward compatible: callers that only pass the first four
    positional args (no payments ledger, no cancellation) get the original draft/paid/unpaid/
    overdue behaviour unchanged."""
    if cancelled:
        return "cancelled"
    if status == "draft":
        return "draft"
    if total_paise > 0 and paid_amount_paise >= total_paise:
        return "paid"
    if paid_amount_paise > 0:
        return "partially_paid"
    if paid_at is not None:
        return "paid"
    if due_date is not None and due_date < today:
        return "overdue"
    return "unpaid"


def plivo_call_cost_paise(
    direction: Literal["inbound", "outbound"],
    billed_seconds: int,
    inbound_inr_per_min: Decimal,
    outbound_inr_per_min: Decimal,
) -> int:
    rate = outbound_inr_per_min if direction == "outbound" else inbound_inr_per_min
    minutes = Decimal(billed_seconds) / Decimal(60)
    return round_paise(minutes * rate)


def speech_usage_cost_paise(quantity: Decimal, rate_inr: Decimal) -> int:
    return round_paise(quantity * rate_inr)
