import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.agent import runner
from app.contracts.contracts import OrderFacts, OrderLine, ToolResult
from app.core.db import session_scope
from app.integrations.shopify_write import FakeShopify, set_writer_override
from app.v1.gate import GateOrder
from tests.agent.helpers import AgentEnv
from tests.channels.helpers import wa_message
from tests.v1.helpers import TOOLS

ADDRESS = {
    "line1": "12 MG Road",
    "city": "Pune",
    "state": "Maharashtra",
    "pin": "411001",
    "country": "IN",
}


def plan(
    intent: str = "cancel",
    action: str = "propose",
    tool: str | None = "order_cancel",
    ref: str | None = "#1001",
    **over: Any,
) -> str:
    args = {"order_ref": ref, **over.pop("args", {})}
    body = {
        "intent": intent,
        "action": action,
        "tool": tool,
        "args": args,
        "confidence": 0.95,
        "sentiment": 0.0,
        "language": "hinglish",
        "summary": f"customer wants {intent}",
        **over,
    }
    return json.dumps(body)


def facts(name: str = "#1001", total: int = 40_000) -> tuple[ToolResult, GateOrder]:
    order = OrderFacts(
        order_name=name,
        financial_status="paid",
        fulfillment_status="unfulfilled",
        lines=[OrderLine(title="Kurta", quantity=1)],
        total_paise=total,
        created_at=datetime.now(UTC),
    )
    gate = GateOrder(
        order_ref=name,
        total_paise=total,
        fulfillment_status="unfulfilled",
        financial_status="paid",
        cancelled=False,
        refunded_paise=0,
        delivered_at=None,
    )
    return ToolResult(tool="order_lookup", ok=True, order=order), gate


@dataclass
class FakeV1Tools:
    orders: dict[str, tuple[ToolResult, GateOrder]] = field(
        default_factory=lambda: {"#1001": facts()}
    )
    errors: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def order_gate(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> tuple[ToolResult, GateOrder | None]:
        name = order_ref if order_ref.startswith("#") else f"#{order_ref}"
        self.calls.append(
            {"tool": "order_lookup", "args": {"order_ref": name}, "ok": True, "error": None}
        )
        if name in self.errors:
            return ToolResult(tool="order_lookup", ok=False, error_code=self.errors[name]), None
        if name not in self.orders:
            return ToolResult(tool="order_lookup", ok=False, error_code="not_found"), None
        return self.orders[name]

    async def order_lookup(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> ToolResult:
        return (await self.order_gate(order_ref, phone_e164, email))[0]

    async def tracking_lookup(self, awb: str, courier_hint: str | None) -> ToolResult:
        return ToolResult(tool="tracking_lookup", ok=False, error_code="not_found")


async def setup(
    env: AgentEnv, level: str = "auto", mode: str = "auto"
) -> tuple[FakeV1Tools, FakeShopify]:
    body = {
        "schema_version": 2,
        "office_hours": {"start": "00:00", "end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]},
        "enabled_tools": ["order_lookup", "tracking_lookup", *TOOLS],
        "autonomy": {t: ("approval" if level == "auto" else level) for t in TOOLS[1:]},
    }
    created = await env.client.post("/policies", json={"content": body}, headers=env.wa.headers)
    assert created.status_code == 201, created.text
    published = await env.client.post(
        f"/policies/{created.json()['id']}/publish", headers=env.wa.headers
    )
    assert published.status_code == 200, published.text
    if level == "auto":
        async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
            await session.execute(
                text(
                    "UPDATE policies SET content = jsonb_set(content, '{autonomy}', CAST(:a AS jsonb))"
                    " WHERE status = 'published'"
                ),
                {"a": json.dumps({t: "auto" for t in TOOLS[1:]})},
            )
    if mode != "shadow":
        await env.set_mode(mode)
    tools = FakeV1Tools()
    shop = FakeShopify()
    shop.add_order("#1001", total_paise=40_000)
    runner.overrides.tools = tools
    set_writer_override(shop)
    return tools, shop


async def say(env: AgentEnv, body: str, mid: str, llm: str | None = None) -> None:
    if llm is not None:
        env.provider.responses = [llm]
    resp = await env.client.post(env.wa.path, json=wa_message(mid, body=body))
    assert resp.status_code == 200
    await env.drain()


def last_reply(env: AgentEnv) -> str:
    return env.sender.sent[-1][1]


async def actions(env: AgentEnv, tool: str | None = None) -> list[Any]:
    sql = "SELECT id, tool_name, status, error, requires_approval, amount_paise FROM agent_actions WHERE action_type = 'tool_call' AND tool_name IN ('address_update','order_cancel','exchange_request','refund_create') ORDER BY created_at"
    rows = await env.q(sql)
    return [r for r in rows if tool is None or r.tool_name == tool]
