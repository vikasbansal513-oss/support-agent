from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.agent.policy_engine import OrderForPolicy, check_return_eligibility, refund_within_limits
from app.v1.contracts import TOOL_TIER, ReasonCodeV1, Tier, WriteTool
from app.v1.policy import PolicyV2

Route = Literal["auto", "approval", "handoff", "decline"]
CANCEL_OK_FINANCIAL = ("paid", "pending", "authorized", "partially_paid")


@dataclass(frozen=True)
class GateOrder:
    order_ref: str
    total_paise: int
    fulfillment_status: str
    financial_status: str
    cancelled: bool
    refunded_paise: int
    delivered_at: datetime | None
    tags: tuple[str, ...] = ()
    disputed: bool = False
    address_already_changed: bool = False
    exchange_already_requested: bool = False
    return_already_requested: bool = False


@dataclass(frozen=True)
class GateContext:
    tool: WriteTool
    order: GateOrder
    now: datetime
    customer_refunded_today_paise: int = 0
    customer_refund_in_last_30d: bool = False
    brand_auto_value_today_paise: int = 0
    killed: bool = False
    write_count: int = 0
    channel: str = "text"
    # Count of this customer's refund/exchange/return claims inside
    # policy.fraud_cluster_window_hours (see app.v1.regate.customer_context). Independent
    # of amount/mode - Section 12.4's claim-clustering signal.
    recent_claim_count: int = 0


CLAIM_TOOLS: tuple[WriteTool, ...] = ("refund_create", "exchange_request", "return_request")


@dataclass(frozen=True)
class GateDecision:
    route: Route
    reason: str
    handoff_reason: ReasonCodeV1 | None = None
    amount_paise: int = 0
    tier: Tier = "T1"


def amount_for(tool: WriteTool, order: GateOrder) -> int:
    if tool == "refund_create":
        return max(0, order.total_paise - order.refunded_paise)
    if tool in ("order_cancel", "exchange_request"):
        return order.total_paise
    return 0


def _handoff(tool: WriteTool, reason: str, code: ReasonCodeV1, amount: int = 0) -> GateDecision:
    return GateDecision("handoff", reason, code, amount, TOOL_TIER[tool])


VOICE_APPROVAL_REASON: dict[str, str] = {
    "refund_create": "VOICE_REFUND",
    "exchange_request": "VOICE_EXCHANGE",
    "return_request": "VOICE_RETURN",
}


def _voice_gate(decision: GateDecision, ctx: GateContext) -> GateDecision:
    if ctx.channel == "voice" and decision.route == "auto":
        reason = VOICE_APPROVAL_REASON.get(ctx.tool)
        if reason is not None:
            return GateDecision("approval", reason, None, decision.amount_paise, decision.tier)
    return decision


def evaluate(policy: PolicyV2, ctx: GateContext) -> GateDecision:
    return _voice_gate(_evaluate(policy, ctx), ctx)


DECLINE_REASONS: frozenset[str] = frozenset(
    {
        "NOT_DELIVERED",
        "WINDOW_EXPIRED",
        "ORDER_CLOSED",
        "ALREADY_REFUNDED",
        "RETURN_EXISTS",
        "EXCHANGE_EXISTS",
    }
)


def resolve(policy: PolicyV2, ctx: GateContext) -> GateDecision:
    """What the agent actually does with a request. Handoff stays only for a tool that is off or
    killed and for the safety stops (claim clustering, write limit, shipped cancel/address,
    final sale, future delivery date). A clear policy answer (not shipped, window over, order
    closed, already requested) becomes a plain customer reply ("decline"), and a risk flag
    (high value, disputed, recent refund, unknown delivery date) goes to the client's Approvals
    once every hard check has passed."""
    decision = _voice_gate(_evaluate(policy, ctx, soft=True), ctx)
    if decision.route == "handoff" and decision.reason in DECLINE_REASONS:
        return GateDecision("decline", decision.reason, None, decision.amount_paise, decision.tier)
    return decision


def _evaluate(policy: PolicyV2, ctx: GateContext, soft: bool = False) -> GateDecision:
    tool, order = ctx.tool, ctx.order
    amount = amount_for(tool, order)
    tier: Tier = TOOL_TIER[tool]
    level = policy.write_level(tool)
    if ctx.killed:
        return _handoff(tool, "TOOL_KILLED", "POLICY_EXCEPTION", amount)
    if level == "off":
        return _handoff(tool, "TOOL_OFF", "POLICY_EXCEPTION", amount)
    if ctx.write_count >= policy.max_write_actions:
        return _handoff(tool, "WRITE_LIMIT", "STEP_LIMIT", amount)
    risk: str | None = None
    if order.disputed:
        if not soft:
            return _handoff(tool, "ORDER_DISPUTED", "HIGH_RISK_FLAG", amount)
        risk = "ORDER_DISPUTED"
    if tool in CLAIM_TOOLS and ctx.recent_claim_count >= policy.fraud_cluster_claim_limit:
        # Section 12.4: claim-clustering - hard block, always to a human, even if every
        # individual rule elsewhere in this gate would otherwise allow auto-approval.
        return _handoff(tool, "CLAIM_CLUSTERING", "FRAUD", amount)
    if order.total_paise >= policy.limits.high_value_threshold_paise:
        if not soft:
            return _handoff(tool, "HIGH_VALUE_ORDER", "HIGH_RISK_FLAG", amount)
        risk = risk or "HIGH_VALUE_ORDER"
    if tool in ("refund_create", "exchange_request") and ctx.customer_refund_in_last_30d:
        if not soft:
            return _handoff(tool, "RECENT_REFUND", "HIGH_RISK_FLAG", amount)
        risk = risk or "RECENT_REFUND"
    if order.cancelled or order.financial_status in ("refunded", "voided"):
        return _handoff(tool, "ORDER_CLOSED", "POLICY_EXCEPTION", amount)

    if tool == "address_update":
        if order.fulfillment_status not in ("unfulfilled", ""):
            return _handoff(tool, "ALREADY_SHIPPED", "POLICY_EXCEPTION", amount)
        if order.address_already_changed:
            return _handoff(tool, "ADDRESS_ALREADY_CHANGED", "POLICY_EXCEPTION", amount)
    elif tool == "order_cancel":
        if order.fulfillment_status not in ("unfulfilled", ""):
            return _handoff(tool, "ALREADY_SHIPPED", "POLICY_EXCEPTION", amount)
        if order.financial_status not in CANCEL_OK_FINANCIAL:
            return _handoff(tool, "FINANCIAL_STATUS", "POLICY_EXCEPTION", amount)
    else:
        eligibility = check_return_eligibility(
            OrderForPolicy(
                delivered_at=order.delivered_at,
                tags=order.tags,
                financial_status=order.financial_status,
                fulfillment_status=order.fulfillment_status,
            ),
            ctx.now,
            policy.return_window_days,
            list(policy.final_sale_tags),
        )
        if eligibility.decision == "needs_human":
            if not (soft and eligibility.reason == "DELIVERY_DATE_UNKNOWN"):
                return _handoff(tool, eligibility.reason, "POLICY_EXCEPTION", amount)
            risk = risk or eligibility.reason
        if eligibility.decision == "not_eligible":
            return _handoff(tool, eligibility.reason, "POLICY_EXCEPTION", amount)
        if tool == "refund_create" and (order.refunded_paise > 0 or amount <= 0):
            return _handoff(tool, "ALREADY_REFUNDED", "POLICY_EXCEPTION", amount)
        if tool == "exchange_request" and order.exchange_already_requested:
            return _handoff(tool, "EXCHANGE_EXISTS", "POLICY_EXCEPTION", amount)
        if tool == "return_request" and order.return_already_requested:
            return _handoff(tool, "RETURN_EXISTS", "POLICY_EXCEPTION", amount)

    if risk is not None:
        return GateDecision("approval", risk, None, amount, tier)
    if level == "approval":
        return GateDecision("approval", "LEVEL_APPROVAL", None, amount, tier)
    return _within_auto(policy, ctx, amount, tier)


def _within_auto(policy: PolicyV2, ctx: GateContext, amount: int, tier: Tier) -> GateDecision:
    tool = ctx.tool
    limits = policy.limits
    if tool == "address_update":
        return GateDecision("auto", "AUTO_OK", None, 0, tier)
    if tool == "return_request":
        # A return moves no money (the later refund is its own refund_create action with its
        # own limits), so its amount stays 0 and it does not count toward the brand's daily
        # auto value. The brand's return_auto_limit caps the ORDER value it may register alone.
        if ctx.order.total_paise > limits.return_auto_limit_paise:
            return GateDecision("approval", "LIMIT_EXCEEDED", None, 0, tier)
        return GateDecision("auto", "AUTO_OK", None, 0, tier)
    cap = limits.auto_limit(tool)
    if cap is None or amount > cap:
        return GateDecision("approval", "LIMIT_EXCEEDED", None, amount, tier)
    if tool == "refund_create" and not refund_within_limits(
        amount,
        ctx.customer_refunded_today_paise,
        limits.refund_auto_limit_paise,
        limits.refund_customer_daily_paise,
    ):
        return GateDecision("approval", "LIMIT_EXCEEDED", None, amount, tier)
    if ctx.brand_auto_value_today_paise + amount > limits.brand_daily_auto_value_paise:
        return GateDecision("approval", "BRAND_DAILY_LIMIT", None, amount, tier)
    return GateDecision("auto", "AUTO_OK", None, amount, tier)
