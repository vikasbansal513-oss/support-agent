import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.actions import writes
from app.core.db import session_scope
from app.integrations.shopify_write import FakeShopify
from app.v1.contracts import WriteTool
from app.v1.gate import GateDecision
from app.v1.policy import PolicyV2
from tests.agent.helpers import AgentEnv

TOOLS = ["handoff_to_human", "address_update", "order_cancel", "exchange_request", "refund_create"]


def policy(level: str = "auto", **over: object) -> PolicyV2:
    return PolicyV2.model_validate(
        {
            "enabled_tools": TOOLS,
            "autonomy": {
                "address_update": level,
                "order_cancel": level,
                "exchange_request": level,
                "refund_create": level,
            },
            **over,
        }
    )


@dataclass
class Conv:
    customer_id: uuid.UUID
    conversation_id: uuid.UUID


async def make_conv(env: AgentEnv, phone: str = "+919800000042") -> Conv:
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        customer = (
            await session.execute(
                text("INSERT INTO customers (phone_e164) VALUES (:p) RETURNING id"), {"p": phone}
            )
        ).scalar_one()
        conv = (
            await session.execute(
                text(
                    "INSERT INTO conversations (customer_id, channel) VALUES (:c, 'whatsapp')"
                    " RETURNING id"
                ),
                {"c": customer},
            )
        ).scalar_one()
    return Conv(uuid.UUID(str(customer)), uuid.UUID(str(conv)))


def decision(route: str = "auto", amount: int = 0) -> GateDecision:
    return GateDecision(route, "TEST", None, amount, "T2")  # type: ignore[arg-type]


async def propose_and_confirm(
    env: AgentEnv,
    conv: Conv,
    pol: PolicyV2,
    tool: WriteTool = "order_cancel",
    order_ref: str = "#1001",
    amount: int = 40_000,
    route: str = "auto",
    n: int = 0,
    args: dict[str, object] | None = None,
) -> writes.ActionRow:
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        proposal = await writes.propose(
            session,
            conversation_id=conv.conversation_id,
            run_id=None,
            tool=tool,
            order_ref=order_ref,
            amount_paise=amount,
            args=args or {"reason": "changed mind"},
            decision=decision(route, amount),
            policy=pol,
            write_count=n,
        )
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        return await writes.confirm(session, proposal.action.id, proposal.token)


def shop(*orders: str, total: int = 40_000) -> FakeShopify:
    fake = FakeShopify()
    for ref in orders or ("#1001",):
        fake.add_order(ref, total_paise=total)
    return fake
