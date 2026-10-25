from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.v1.contracts import WriteTool
from app.v1.gate import GateContext, GateOrder, evaluate
from app.v1.policy import PolicyV2

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
WRITE_TOOLS: tuple[WriteTool, ...] = (
    "address_update",
    "order_cancel",
    "exchange_request",
    "refund_create",
)
ENABLED = ["handoff_to_human", "order_lookup", "tracking_lookup", *WRITE_TOOLS]
CANCEL_CAP = 200_000
EXCHANGE_CAP = 200_000
REFUND_CAP = 50_000
REFUND_DAILY = 100_000
BRAND_DAILY = 2_000_000
HIGH_VALUE = 500_000


@dataclass(frozen=True)
class GateScenario:
    name: str
    tool: WriteTool
    expected: tuple[str, str]
    level: str = "auto"
    order: dict[str, Any] = field(default_factory=dict)
    ctx: dict[str, Any] = field(default_factory=dict)
    shipped: bool = False


def _policy(level: str) -> PolicyV2:
    return PolicyV2.model_validate(
        {
            "enabled_tools": ENABLED,
            "autonomy": {tool: level for tool in WRITE_TOOLS},
            "limits": {
                "cancel_auto_limit_paise": CANCEL_CAP,
                "exchange_auto_limit_paise": EXCHANGE_CAP,
                "refund_auto_limit_paise": REFUND_CAP,
                "refund_customer_daily_paise": REFUND_DAILY,
                "brand_daily_auto_value_paise": BRAND_DAILY,
                "high_value_threshold_paise": HIGH_VALUE,
            },
            "return_window_days": 7,
            "final_sale_tags": ["clearance"],
        }
    )


def _order(shipped: bool, over: dict[str, Any]) -> GateOrder:
    base = GateOrder(
        order_ref="#1001",
        total_paise=40_000,
        fulfillment_status="fulfilled" if shipped else "unfulfilled",
        financial_status="paid",
        cancelled=False,
        refunded_paise=0,
        delivered_at=NOW - timedelta(days=2) if shipped else None,
    )
    return replace(base, **over)


def run_one(scenario: GateScenario) -> tuple[str, str]:
    order = _order(scenario.shipped, scenario.order)
    ctx = GateContext(tool=scenario.tool, order=order, now=NOW, **scenario.ctx)
    decision = evaluate(_policy(scenario.level), ctx)
    return decision.route, decision.reason


def _common(tool: WriteTool, shipped: bool) -> list[GateScenario]:
    out: list[GateScenario] = []

    def add(name: str, expected: tuple[str, str], **kw: Any) -> None:
        out.append(GateScenario(f"{tool}:{name}", tool, expected, shipped=shipped, **kw))

    add("level_off", ("handoff", "TOOL_OFF"), level="off")
    add("level_off_killed_wins", ("handoff", "TOOL_KILLED"), level="off", ctx={"killed": True})
    add("killed", ("handoff", "TOOL_KILLED"), ctx={"killed": True})
    add("killed_approval_level", ("handoff", "TOOL_KILLED"), level="approval", ctx={"killed": True})
    for writes in (3, 4, 10):
        add(f"write_count_{writes}", ("handoff", "WRITE_LIMIT"), ctx={"write_count": writes})
    for writes in (0, 1, 2):
        add(
            f"write_count_{writes}_ok",
            ("approval", "LEVEL_APPROVAL"),
            level="approval",
            ctx={"write_count": writes},
        )
    add("disputed", ("handoff", "ORDER_DISPUTED"), order={"disputed": True})
    add(
        "disputed_approval_level",
        ("handoff", "ORDER_DISPUTED"),
        level="approval",
        order={"disputed": True},
    )
    add("cancelled", ("handoff", "ORDER_CLOSED"), order={"cancelled": True})
    for status in ("refunded", "voided"):
        add(f"financial_{status}", ("handoff", "ORDER_CLOSED"), order={"financial_status": status})
    add(
        "high_value_at_threshold",
        ("handoff", "HIGH_VALUE_ORDER"),
        order={"total_paise": HIGH_VALUE},
    )
    add("high_value_above", ("handoff", "HIGH_VALUE_ORDER"), order={"total_paise": HIGH_VALUE * 4})
    add(
        "high_value_approval_level_still_handoff",
        ("handoff", "HIGH_VALUE_ORDER"),
        level="approval",
        order={"total_paise": HIGH_VALUE + 1},
    )
    add("approval_level", ("approval", "LEVEL_APPROVAL"), level="approval")
    add(
        "disputed_beats_high_value",
        ("handoff", "ORDER_DISPUTED"),
        order={"disputed": True, "total_paise": HIGH_VALUE},
    )
    return out


def _cancel() -> list[GateScenario]:
    tool: WriteTool = "order_cancel"
    out = _common(tool, False)
    for status in ("fulfilled", "partial", "shipped", "in_transit", "delivered"):
        out.append(
            GateScenario(
                f"{tool}:fulfillment_{status}",
                tool,
                ("handoff", "ALREADY_SHIPPED"),
                order={"fulfillment_status": status},
            )
        )
    for status in ("paid", "pending", "authorized", "partially_paid"):
        out.append(
            GateScenario(
                f"{tool}:financial_{status}_ok",
                tool,
                ("auto", "AUTO_OK"),
                order={"financial_status": status},
            )
        )
    for status in ("partially_refunded", "unpaid", "expired"):
        out.append(
            GateScenario(
                f"{tool}:financial_{status}",
                tool,
                ("handoff", "FINANCIAL_STATUS"),
                order={"financial_status": status},
            )
        )
    for total in (1, 50_000, CANCEL_CAP - 1, CANCEL_CAP):
        out.append(
            GateScenario(
                f"{tool}:total_{total}_auto",
                tool,
                ("auto", "AUTO_OK"),
                order={"total_paise": total},
            )
        )
    for total in (CANCEL_CAP + 1, 300_000, HIGH_VALUE - 1):
        out.append(
            GateScenario(
                f"{tool}:total_{total}_over_cap",
                tool,
                ("approval", "LIMIT_EXCEEDED"),
                order={"total_paise": total},
            )
        )
    for used in (0, 1_000_000, BRAND_DAILY - CANCEL_CAP):
        out.append(
            GateScenario(
                f"{tool}:brand_used_{used}_ok",
                tool,
                ("auto", "AUTO_OK"),
                order={"total_paise": CANCEL_CAP},
                ctx={"brand_auto_value_today_paise": used},
            )
        )
    for used in (BRAND_DAILY - CANCEL_CAP + 1, BRAND_DAILY, BRAND_DAILY + 5):
        out.append(
            GateScenario(
                f"{tool}:brand_used_{used}_over",
                tool,
                ("approval", "BRAND_DAILY_LIMIT"),
                order={"total_paise": CANCEL_CAP},
                ctx={"brand_auto_value_today_paise": used},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:empty_fulfillment_ok",
            tool,
            ("auto", "AUTO_OK"),
            order={"fulfillment_status": ""},
        )
    )
    for status in ("paid", "pending", "authorized", "partially_paid"):
        out.append(
            GateScenario(
                f"{tool}:approval_level_{status}",
                tool,
                ("approval", "LEVEL_APPROVAL"),
                level="approval",
                order={"financial_status": status},
            )
        )
    for status in ("fulfilled", "partial"):
        out.append(
            GateScenario(
                f"{tool}:approval_level_{status}_blocked",
                tool,
                ("handoff", "ALREADY_SHIPPED"),
                level="approval",
                order={"fulfillment_status": status},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:recent_refund_does_not_block_cancel",
            tool,
            ("auto", "AUTO_OK"),
            ctx={"customer_refund_in_last_30d": True},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:cancel_ignores_refund_daily",
            tool,
            ("auto", "AUTO_OK"),
            ctx={"customer_refunded_today_paise": REFUND_DAILY * 5},
        )
    )
    return out


def _address() -> list[GateScenario]:
    tool: WriteTool = "address_update"
    out = _common(tool, False)
    for status in ("fulfilled", "partial", "shipped", "in_transit", "delivered"):
        out.append(
            GateScenario(
                f"{tool}:fulfillment_{status}",
                tool,
                ("handoff", "ALREADY_SHIPPED"),
                order={"fulfillment_status": status},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:already_changed",
            tool,
            ("handoff", "ADDRESS_ALREADY_CHANGED"),
            order={"address_already_changed": True},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:already_changed_approval_level",
            tool,
            ("handoff", "ADDRESS_ALREADY_CHANGED"),
            level="approval",
            order={"address_already_changed": True},
        )
    )
    for total in (1, 1_000, 40_000, 200_000, 400_000, HIGH_VALUE - 1):
        out.append(
            GateScenario(
                f"{tool}:total_{total}_auto",
                tool,
                ("auto", "AUTO_OK"),
                order={"total_paise": total},
            )
        )
    for status in ("paid", "pending", "authorized", "partially_paid", "unpaid"):
        out.append(
            GateScenario(
                f"{tool}:financial_{status}_ok",
                tool,
                ("auto", "AUTO_OK"),
                order={"financial_status": status},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:brand_daily_does_not_apply",
            tool,
            ("auto", "AUTO_OK"),
            ctx={"brand_auto_value_today_paise": BRAND_DAILY * 3},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:amount_is_zero_no_limit",
            tool,
            ("auto", "AUTO_OK"),
            order={"total_paise": HIGH_VALUE - 1},
            ctx={"customer_refunded_today_paise": 10**7},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:recent_refund_does_not_block",
            tool,
            ("auto", "AUTO_OK"),
            ctx={"customer_refund_in_last_30d": True},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:empty_fulfillment_ok",
            tool,
            ("auto", "AUTO_OK"),
            order={"fulfillment_status": ""},
        )
    )
    for days in (0, 3):
        out.append(
            GateScenario(
                f"{tool}:delivered_{days}d_ago_is_shipped",
                tool,
                ("handoff", "ALREADY_SHIPPED"),
                shipped=True,
            )
        )
    for total in (1, 40_000, HIGH_VALUE - 1):
        out.append(
            GateScenario(
                f"{tool}:approval_level_total_{total}",
                tool,
                ("approval", "LEVEL_APPROVAL"),
                level="approval",
                order={"total_paise": total},
            )
        )
    for status in ("fulfilled", "partial"):
        out.append(
            GateScenario(
                f"{tool}:approval_level_{status}_still_blocked",
                tool,
                ("handoff", "ALREADY_SHIPPED"),
                level="approval",
                order={"fulfillment_status": status},
            )
        )
    for total in (1, 40_000, HIGH_VALUE - 1):
        out.append(
            GateScenario(
                f"{tool}:off_level_total_{total}",
                tool,
                ("handoff", "TOOL_OFF"),
                level="off",
                order={"total_paise": total},
            )
        )
    return out


def _delivery_cases(tool: WriteTool, cap: int) -> list[GateScenario]:
    out = _common(tool, True)
    for days in (0, 1, 6, 7):
        out.append(
            GateScenario(
                f"{tool}:delivered_{days}d_ago_in_window",
                tool,
                ("auto", "AUTO_OK"),
                order={"delivered_at": NOW - timedelta(days=days)},
            )
        )
    for days in (8, 9, 30, 400):
        out.append(
            GateScenario(
                f"{tool}:delivered_{days}d_ago_expired",
                tool,
                ("handoff", "WINDOW_EXPIRED"),
                order={"delivered_at": NOW - timedelta(days=days)},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:delivery_date_unknown",
            tool,
            ("handoff", "DELIVERY_DATE_UNKNOWN"),
            order={"delivered_at": None},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:delivery_in_future",
            tool,
            ("handoff", "DELIVERY_IN_FUTURE"),
            order={"delivered_at": NOW + timedelta(days=2)},
        )
    )
    for status in ("unfulfilled", "", "restocked"):
        out.append(
            GateScenario(
                f"{tool}:not_delivered_{status or 'empty'}",
                tool,
                ("handoff", "NOT_DELIVERED"),
                order={"fulfillment_status": status},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:partial_fulfillment_ok",
            tool,
            ("auto", "AUTO_OK"),
            order={"fulfillment_status": "partial"},
        )
    )
    for tag in ("clearance", "CLEARANCE", " Clearance "):
        out.append(
            GateScenario(
                f"{tool}:final_sale_tag_{tag.strip().lower()}_{len(tag)}",
                tool,
                ("handoff", "FINAL_SALE"),
                order={"tags": (tag,)},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:unrelated_tag_ok", tool, ("auto", "AUTO_OK"), order={"tags": ("new", "vip")}
        )
    )
    out.append(
        GateScenario(
            f"{tool}:final_sale_beats_expired",
            tool,
            ("handoff", "FINAL_SALE"),
            order={"tags": ("clearance",), "delivered_at": NOW - timedelta(days=60)},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:recent_refund_30d",
            tool,
            ("handoff", "RECENT_REFUND"),
            ctx={"customer_refund_in_last_30d": True},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:recent_refund_30d_approval_level",
            tool,
            ("handoff", "RECENT_REFUND"),
            level="approval",
            ctx={"customer_refund_in_last_30d": True},
        )
    )
    for used in (0, 1_000_000, BRAND_DAILY - cap):
        out.append(
            GateScenario(
                f"{tool}:brand_used_{used}_ok",
                tool,
                ("auto", "AUTO_OK"),
                order={"total_paise": cap},
                ctx={"brand_auto_value_today_paise": used},
            )
        )
    for used in (BRAND_DAILY - cap + 1, BRAND_DAILY):
        out.append(
            GateScenario(
                f"{tool}:brand_used_{used}_over",
                tool,
                ("approval", "BRAND_DAILY_LIMIT"),
                order={"total_paise": cap},
                ctx={"brand_auto_value_today_paise": used},
            )
        )
    return out


def _exchange() -> list[GateScenario]:
    tool: WriteTool = "exchange_request"
    out = [replace(x, shipped=True) for x in _delivery_cases(tool, EXCHANGE_CAP)]
    for total in (1, 50_000, EXCHANGE_CAP - 1, EXCHANGE_CAP):
        out.append(
            GateScenario(
                f"{tool}:total_{total}_auto",
                tool,
                ("auto", "AUTO_OK"),
                order={"total_paise": total},
            )
        )
    for total in (EXCHANGE_CAP + 1, 300_000, HIGH_VALUE - 1):
        out.append(
            GateScenario(
                f"{tool}:total_{total}_over_cap",
                tool,
                ("approval", "LIMIT_EXCEEDED"),
                order={"total_paise": total},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:exchange_exists",
            tool,
            ("handoff", "EXCHANGE_EXISTS"),
            order={"exchange_already_requested": True},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:financial_refunded",
            tool,
            ("handoff", "ORDER_CLOSED"),
            order={"financial_status": "refunded"},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:partially_refunded_order_still_exchangeable",
            tool,
            ("auto", "AUTO_OK"),
            order={"refunded_paise": 10_000},
        )
    )
    return [replace(x, shipped=True) for x in out]


def _refund() -> list[GateScenario]:
    tool: WriteTool = "refund_create"
    out = [replace(x, shipped=True) for x in _delivery_cases(tool, REFUND_CAP)]
    for total in (1, 10_000, REFUND_CAP - 1, REFUND_CAP):
        out.append(
            GateScenario(
                f"{tool}:total_{total}_auto",
                tool,
                ("auto", "AUTO_OK"),
                order={"total_paise": total},
            )
        )
    for total in (REFUND_CAP + 1, 100_000, HIGH_VALUE - 1):
        out.append(
            GateScenario(
                f"{tool}:total_{total}_over_cap",
                tool,
                ("approval", "LIMIT_EXCEEDED"),
                order={"total_paise": total},
            )
        )
    for today in (0, REFUND_DAILY - REFUND_CAP - 0):
        out.append(
            GateScenario(
                f"{tool}:customer_today_{today}_ok",
                tool,
                ("auto", "AUTO_OK"),
                order={"total_paise": REFUND_CAP},
                ctx={"customer_refunded_today_paise": today},
            )
        )
    for today in (REFUND_DAILY - REFUND_CAP + 1, REFUND_DAILY, REFUND_DAILY * 3):
        out.append(
            GateScenario(
                f"{tool}:customer_today_{today}_over",
                tool,
                ("approval", "LIMIT_EXCEEDED"),
                order={"total_paise": REFUND_CAP},
                ctx={"customer_refunded_today_paise": today},
            )
        )
    out.append(
        GateScenario(
            f"{tool}:already_partly_refunded",
            tool,
            ("handoff", "ALREADY_REFUNDED"),
            order={"refunded_paise": 1},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:fully_refunded_amount",
            tool,
            ("handoff", "ALREADY_REFUNDED"),
            order={"refunded_paise": 40_000},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:financial_refunded",
            tool,
            ("handoff", "ORDER_CLOSED"),
            order={"financial_status": "refunded"},
        )
    )
    out.append(
        GateScenario(
            f"{tool}:financial_voided",
            tool,
            ("handoff", "ORDER_CLOSED"),
            order={"financial_status": "voided"},
        )
    )
    return [replace(x, shipped=True) for x in out]


def build() -> list[GateScenario]:
    return [*_address(), *_cancel(), *_exchange(), *_refund()]


SCENARIOS = build()


@dataclass(frozen=True)
class GateReport:
    total: int
    per_tool: dict[str, int]
    failed: list[str]
    wrong_money_auto: list[str]


def run_all() -> GateReport:
    failed: list[str] = []
    wrong_money: list[str] = []
    per_tool: dict[str, int] = {}
    for scenario in SCENARIOS:
        per_tool[scenario.tool] = per_tool.get(scenario.tool, 0) + 1
        try:
            got = run_one(scenario)
        except Exception:
            failed.append(scenario.name)
            continue
        if got != scenario.expected:
            failed.append(scenario.name)
            if got[0] == "auto" and scenario.expected[0] != "auto":
                wrong_money.append(scenario.name)
    return GateReport(len(SCENARIOS), per_tool, failed, wrong_money)
