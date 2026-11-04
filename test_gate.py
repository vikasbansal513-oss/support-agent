from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.v1.contracts import WriteTool
from app.v1.gate import GateContext, GateOrder, amount_for, evaluate
from app.v1.policy import PolicyV2

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
TOOLS = [
    "handoff_to_human",
    "address_update",
    "order_cancel",
    "exchange_request",
    "refund_create",
    "return_request",
]


def policy(level: str = "auto", **limits: int) -> PolicyV2:
    return PolicyV2.model_validate(
        {
            "enabled_tools": TOOLS,
            "autonomy": {
                "address_update": level,
                "order_cancel": level,
                "exchange_request": level,
                "refund_create": level,
                "return_request": level,
            },
            "limits": limits,
        }
    )


def order(**over: object) -> GateOrder:
    base = GateOrder(
        order_ref="#1001",
        total_paise=40_000,
        fulfillment_status="unfulfilled",
        financial_status="paid",
        cancelled=False,
        refunded_paise=0,
        delivered_at=None,
    )
    return replace(base, **over)  # type: ignore[arg-type]


def delivered(**over: object) -> GateOrder:
    return order(fulfillment_status="fulfilled", delivered_at=NOW - timedelta(days=2), **over)


def ctx(tool: WriteTool, o: GateOrder, **over: object) -> GateContext:
    return GateContext(tool=tool, order=o, now=NOW, **over)  # type: ignore[arg-type]


def test_level_off_and_killed_always_handoff() -> None:
    assert (
        evaluate(policy("off"), ctx("order_cancel", order())).handoff_reason == "POLICY_EXCEPTION"
    )
    killed = evaluate(policy("auto"), ctx("order_cancel", order(), killed=True))
    assert killed.route == "handoff" and killed.reason == "TOOL_KILLED"


def test_level_approval_routes_to_approval_after_checks() -> None:
    d = evaluate(policy("approval"), ctx("order_cancel", order()))
    assert (d.route, d.amount_paise, d.tier) == ("approval", 40_000, "T2")
    shipped = evaluate(
        policy("approval"), ctx("order_cancel", order(fulfillment_status="fulfilled"))
    )
    assert shipped.route == "handoff" and shipped.reason == "ALREADY_SHIPPED"


def test_cancel_auto_within_limit_and_boundary() -> None:
    p = policy("auto", cancel_auto_limit_paise=40_000)
    assert evaluate(p, ctx("order_cancel", order(total_paise=40_000))).route == "auto"
    over = evaluate(p, ctx("order_cancel", order(total_paise=40_001)))
    assert over.route == "approval" and over.reason == "LIMIT_EXCEEDED"


def test_address_update_auto_and_rules() -> None:
    assert evaluate(policy(), ctx("address_update", order())).route == "auto"
    assert (
        evaluate(policy(), ctx("address_update", order(fulfillment_status="fulfilled"))).reason
        == "ALREADY_SHIPPED"
    )
    assert (
        evaluate(policy(), ctx("address_update", order(address_already_changed=True))).reason
        == "ADDRESS_ALREADY_CHANGED"
    )
    assert evaluate(policy(), ctx("address_update", order(cancelled=True))).reason == "ORDER_CLOSED"


def test_refund_amount_comes_from_order_not_input() -> None:
    o = delivered(total_paise=30_000)
    d = evaluate(policy("auto"), ctx("refund_create", o))
    assert d.amount_paise == 30_000 == amount_for("refund_create", o)
    assert d.route == "auto"


@pytest.mark.parametrize(
    ("total", "today", "route"),
    [
        (50_000, 0, "auto"),
        (50_001, 0, "approval"),
        (30_000, 70_000, "auto"),
        (30_000, 70_001, "approval"),
    ],
)
def test_refund_limit_boundaries(total: int, today: int, route: str) -> None:
    p = policy("auto", refund_auto_limit_paise=50_000, refund_customer_daily_paise=100_000)
    d = evaluate(
        p, ctx("refund_create", delivered(total_paise=total), customer_refunded_today_paise=today)
    )
    assert d.route == route


def test_refund_window_final_sale_and_double_refund() -> None:
    late = order(fulfillment_status="fulfilled", delivered_at=NOW - timedelta(days=30))
    assert evaluate(policy(), ctx("refund_create", late)).reason == "WINDOW_EXPIRED"
    tagged = delivered(tags=("final-sale",))
    p = PolicyV2.model_validate(
        {
            "enabled_tools": TOOLS,
            "final_sale_tags": ["final-sale"],
            "autonomy": {"refund_create": "auto"},
        }
    )
    assert evaluate(p, ctx("refund_create", tagged)).reason == "FINAL_SALE"
    again = evaluate(policy(), ctx("refund_create", delivered(refunded_paise=100)))
    assert again.route == "handoff"
    unknown = order(fulfillment_status="fulfilled", delivered_at=None)
    assert evaluate(policy(), ctx("refund_create", unknown)).reason == "DELIVERY_DATE_UNKNOWN"


def test_never_delivered_cannot_refund() -> None:
    d = evaluate(policy(), ctx("refund_create", order()))
    assert d.route == "handoff" and d.reason == "NOT_DELIVERED"


def test_high_risk_flags_force_handoff_even_when_auto() -> None:
    assert (
        evaluate(policy(), ctx("order_cancel", order(disputed=True))).handoff_reason
        == "HIGH_RISK_FLAG"
    )
    big = order(total_paise=500_000)
    assert evaluate(policy(), ctx("order_cancel", big)).reason == "HIGH_VALUE_ORDER"
    recent = evaluate(policy(), ctx("refund_create", delivered(), customer_refund_in_last_30d=True))
    assert recent.reason == "RECENT_REFUND" and recent.handoff_reason == "HIGH_RISK_FLAG"


def test_brand_daily_limit_downgrades_to_approval() -> None:
    p = policy(
        "auto",
        brand_daily_auto_value_paise=100_000,
        cancel_auto_limit_paise=50_000,
        exchange_auto_limit_paise=50_000,
    )
    d = evaluate(
        p, ctx("order_cancel", order(total_paise=40_000), brand_auto_value_today_paise=60_001)
    )
    assert d.route == "approval" and d.reason == "BRAND_DAILY_LIMIT"
    ok = evaluate(
        p, ctx("order_cancel", order(total_paise=40_000), brand_auto_value_today_paise=60_000)
    )
    assert ok.route == "auto"


def test_write_count_limit_hands_off() -> None:
    d = evaluate(policy(), ctx("order_cancel", order(), write_count=3))
    assert d.route == "handoff" and d.handoff_reason == "STEP_LIMIT"


def test_exchange_requires_window_and_no_prior_exchange() -> None:
    assert evaluate(policy(), ctx("exchange_request", delivered())).route == "auto"
    assert (
        evaluate(
            policy(), ctx("exchange_request", delivered(exchange_already_requested=True))
        ).reason
        == "EXCHANGE_EXISTS"
    )


def test_cancel_financial_status_rules() -> None:
    assert (
        evaluate(policy(), ctx("order_cancel", order(financial_status="pending"))).route == "auto"
    )
    assert (
        evaluate(policy(), ctx("order_cancel", order(financial_status="partially_refunded"))).reason
        == "FINANCIAL_STATUS"
    )


def test_claim_cluster_hard_blocks_regardless_of_level_or_limits() -> None:
    # Decision Framework Section 12.4: claim-clustering overrides mode and ₹ limits both.
    p = policy("auto", refund_auto_limit_paise=50_000)
    under = evaluate(p, ctx("refund_create", delivered(), recent_claim_count=2))
    assert under.route == "auto"
    at_limit = evaluate(p, ctx("refund_create", delivered(), recent_claim_count=3))
    assert at_limit.route == "handoff" and at_limit.handoff_reason == "FRAUD"
    assert at_limit.reason == "CLAIM_CLUSTERING"
    approval_level = evaluate(
        policy("approval"), ctx("refund_create", delivered(), recent_claim_count=3)
    )
    assert approval_level.route == "handoff" and approval_level.reason == "CLAIM_CLUSTERING"


def test_claim_cluster_applies_to_every_claim_tool_not_address_update() -> None:
    for tool in ("refund_create", "exchange_request", "return_request"):
        d = evaluate(policy("auto"), ctx(tool, delivered(), recent_claim_count=3))
        assert d.route == "handoff" and d.reason == "CLAIM_CLUSTERING"
    # address_update never moves money and is outside CLAIM_TOOLS - the signal must not
    # leak into a tool the framework never asked it to gate.
    addr = evaluate(policy("auto"), ctx("address_update", order(), recent_claim_count=3))
    assert addr.route == "auto"
