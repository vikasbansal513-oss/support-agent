import uuid
from datetime import date, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import writes
from app.core import timeutil
from app.core.db import session_scope
from app.integrations.shopify_write import ShopifyWriter
from app.v1.gate import GateContext, GateDecision, GateOrder, amount_for, evaluate
from app.v1.policy import PolicyV2

Factory = async_sessionmaker[AsyncSession]


class GateFacts(Protocol):
    async def order(self, order_ref: str) -> GateOrder | None: ...


class WriterFacts:
    """Facts from the write client's own read. Delivery date, tags and dispute flags are not
    available there, so return/refund eligibility stays closed until a richer provider is set."""

    def __init__(self, writer: ShopifyWriter) -> None:
        self._writer = writer

    async def order(self, order_ref: str) -> GateOrder | None:
        raw = await self._writer.read_order(order_ref)
        if raw is None:
            return None
        return GateOrder(
            order_ref=raw.order_ref,
            total_paise=raw.total_paise,
            fulfillment_status=raw.fulfillment_status,
            financial_status="paid",
            cancelled=raw.cancelled,
            refunded_paise=raw.refunded_paise,
            delivered_at=None,
            exchange_already_requested=raw.exchange_requested,
            return_already_requested=raw.return_requested,
        )


class ReadFirstFacts:
    """Fresh facts from the read integration (delivery date, tags, disputes), same customer-match
    rule as the first lookup. Falls back to the writer's own read, which keeps return tools closed."""

    def __init__(self, primary: GateFacts, fallback: GateFacts) -> None:
        self._primary, self._fallback = primary, fallback

    async def order(self, order_ref: str) -> GateOrder | None:
        found = await self._primary.order(order_ref)
        return found if found is not None else await self._fallback.order(order_ref)


_facts_override: GateFacts | None = None


def set_facts_override(facts: GateFacts | None) -> None:
    global _facts_override
    _facts_override = facts


def get_facts(writer: ShopifyWriter) -> GateFacts:
    return _facts_override if _facts_override is not None else WriterFacts(writer)


async def customer_context(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    tool: str,
    day: date,
    fraud_cluster_window_hours: int = 24,
) -> tuple[int, bool, int, int]:
    customer = await writes._customer_of(session, conversation_id)
    refunded_today = await writes.customer_usage_today(session, customer, "refund_create", day)
    recent = (
        await session.execute(
            text(
                "SELECT 1 FROM agent_actions a JOIN conversations c ON c.id = a.conversation_id"
                " WHERE c.customer_id = :c AND a.tool_name = 'refund_create'"
                " AND a.status IN ('executed','confirmed') AND a.created_at >= :s LIMIT 1"
            ),
            {"c": customer, "s": timeutil.utc_now() - timedelta(days=30)},
        )
    ).first()
    brand = await writes.brand_auto_value_today(session, day)
    recent_claims = await claim_cluster_count(session, customer, fraud_cluster_window_hours)
    return refunded_today, recent is not None, brand, recent_claims


async def claim_cluster_count(
    session: AsyncSession, customer_id: uuid.UUID, window_hours: int
) -> int:
    """How many refund/exchange/return proposals this customer has made in the last
    window_hours, of any status - Section 12.4's claim-clustering signal. Deliberately not
    scoped to a single order: the documented fraud pattern is several DIFFERENT orders'
    claims clustering together, not repeats on one order."""
    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM agent_actions a JOIN conversations c"
                " ON c.id = a.conversation_id WHERE c.customer_id = :c"
                " AND a.tool_name IN ('refund_create','exchange_request','return_request')"
                " AND a.created_at >= :s"
            ),
            {"c": customer_id, "s": timeutil.utc_now() - timedelta(hours=window_hours)},
        )
    ).scalar_one()
    return int(count)


async def regate(
    factory: Factory,
    tenant_id: uuid.UUID,
    facts: GateFacts,
    policy: PolicyV2,
    action: writes.ActionRow,
) -> GateDecision:
    order = await facts.order(action.order_ref)
    if order is None:
        return GateDecision("handoff", "ORDER_NOT_FOUND", "ACTION_FAILED", 0)
    now = timeutil.utc_now()
    day = timeutil.to_ist(now).date()
    async with session_scope(factory, tenant_id=tenant_id) as session:
        refunded_today, recent, brand, recent_claims = await customer_context(
            session, action.conversation_id, action.tool, day, policy.fraud_cluster_window_hours
        )
        killed = await writes.killed(session, action.tool)
    ctx = GateContext(
        tool=action.tool,
        order=order,
        now=now,
        customer_refunded_today_paise=refunded_today,
        customer_refund_in_last_30d=recent,
        brand_auto_value_today_paise=brand,
        killed=killed,
        write_count=0,
        recent_claim_count=recent_claims,
    )
    decision = evaluate(policy, ctx)
    if decision.route != "handoff" and amount_for(action.tool, order) != action.amount_paise:
        return GateDecision("handoff", "ORDER_CHANGED", "POLICY_EXCEPTION", decision.amount_paise)
    return decision


async def facts_for(
    factory: Factory, tenant_id: uuid.UUID, conversation_id: uuid.UUID, writer: ShopifyWriter
) -> GateFacts:
    if _facts_override is not None:
        return _facts_override
    from app.agent.runner import _tool_box
    from app.v1.toolbox import ToolBoxFacts

    fallback = WriterFacts(writer)
    box = await _tool_box(factory, tenant_id, None)
    if not hasattr(box, "order_gate"):
        return fallback
    async with session_scope(factory, tenant_id=tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT cu.phone_e164, cu.email_lower FROM conversations c"
                    " JOIN customers cu ON cu.id = c.customer_id WHERE c.id = :c"
                ),
                {"c": conversation_id},
            )
        ).first()
    if row is None:
        return fallback
    return ReadFirstFacts(ToolBoxFacts(box, row.phone_e164, row.email_lower), fallback)  # type: ignore[arg-type]
