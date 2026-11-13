from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core import officehours
from app.v1.contracts import WriteTool
from app.v1.gate import GateContext, GateOrder, resolve
from app.v1.policy import PolicyV2


class TryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: WriteTool
    order_total_rupees: int = Field(ge=0, le=10_000_000)
    fulfillment_status: Literal["unfulfilled", "fulfilled"]
    delivered_days_ago: int | None = Field(default=None, ge=0, le=3650)
    tags: list[str] = Field(default_factory=list, max_length=20)
    refunded_in_last_30_days: bool = False
    at: AwareDatetime | None = None


class TryOut(BaseModel):
    route: str
    reason: str
    handoff_reason: str | None
    amount_paise: int
    return_window_days: int
    office_open: bool
    next_open: AwareDatetime | None


def run(policy: PolicyV2, body: TryIn, tz: str, now: datetime) -> TryOut:
    moment = body.at or now
    delivered = (
        moment - timedelta(days=body.delivered_days_ago)
        if body.fulfillment_status == "fulfilled" and body.delivered_days_ago is not None
        else None
    )
    tags = tuple(" ".join(t.lower().split()) for t in body.tags if t.strip())
    order = GateOrder(
        order_ref="#TEST",
        total_paise=body.order_total_rupees * 100,
        fulfillment_status=body.fulfillment_status,
        financial_status="paid",
        cancelled=False,
        refunded_paise=0,
        delivered_at=delivered,
        tags=tags,
    )
    ctx = GateContext(
        tool=body.tool,
        order=order,
        now=moment,
        customer_refund_in_last_30d=body.refunded_in_last_30_days,
    )
    decision = resolve(policy, ctx)
    is_open = officehours.is_open(policy, moment, tz)
    opens = None if is_open else officehours.next_open(policy, moment, tz)
    return TryOut(
        route=decision.route,
        reason=decision.reason,
        handoff_reason=decision.handoff_reason,
        amount_paise=decision.amount_paise,
        return_window_days=policy.return_window_for(tags),
        office_open=is_open,
        next_open=opens.astimezone(UTC) if opens else None,
    )
