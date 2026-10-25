"""Pure policy functions. All time logic is IST (Asia/Kolkata). No I/O. Used by the agent, API validation and the regression gate."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class OrderForPolicy:
    delivered_at: datetime | None
    tags: tuple[str, ...] = ()
    financial_status: str = "paid"
    fulfillment_status: str = "fulfilled"


@dataclass(frozen=True)
class Eligibility:
    decision: Literal["eligible", "not_eligible", "needs_human"]
    reason: str


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return dt.astimezone(IST)


def check_return_eligibility(order: OrderForPolicy, now: datetime, return_window_days: int,
                             final_sale_tags: list[str]) -> Eligibility:
    """Window counts IST calendar days from delivery date; the delivery day is day 0; day N is still eligible."""
    if order.financial_status in ("refunded", "voided"):
        return Eligibility("not_eligible", "ALREADY_REFUNDED")
    if order.fulfillment_status not in ("fulfilled", "partial"):
        return Eligibility("not_eligible", "NOT_DELIVERED")
    if order.delivered_at is None:
        return Eligibility("needs_human", "DELIVERY_DATE_UNKNOWN")
    tags = {" ".join(t.lower().split()) for t in order.tags}
    if tags & {" ".join(t.lower().split()) for t in final_sale_tags}:
        return Eligibility("not_eligible", "FINAL_SALE")
    days = (to_ist(now).date() - to_ist(order.delivered_at).date()).days
    if days < 0:
        return Eligibility("needs_human", "DELIVERY_IN_FUTURE")
    if days <= return_window_days:
        return Eligibility("eligible", "WITHIN_WINDOW")
    return Eligibility("not_eligible", "WINDOW_EXPIRED")


def within_office_hours(now: datetime, start: str, end: str, days: list[int]) -> bool:
    n = to_ist(now)
    if n.weekday() not in days:
        return False
    return time.fromisoformat(start) <= n.time().replace(second=0, microsecond=0) < time.fromisoformat(end)


def refund_within_limits(amount_paise: int, customer_refunded_today_paise: int, auto_limit: int | None,
                         daily_limit: int | None) -> bool:
    """Auto refund is OFF unless both limits are set. Amounts are integer paise."""
    if auto_limit is None or daily_limit is None or amount_paise <= 0:
        return False
    return amount_paise <= auto_limit and customer_refunded_today_paise + amount_paise <= daily_limit
