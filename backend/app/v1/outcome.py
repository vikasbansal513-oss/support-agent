import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine, writes
from app.actions.senders import Senders
from app.agent.validate import pick_language
from app.contracts.contracts import OrderFacts
from app.core.db import session_scope
from app.core.mail import Mailer
from app.handoffs import service as handoffs
from app.policy import service as policy_service
from app.v1 import templates as t
from app.v1.contracts import ConvUpdate, DecisionV1, HandoffSummaryV1
from app.v1.policy import PolicyV2

Factory = async_sessionmaker[AsyncSession]
HANDOFF_FOR = {
    "failed": "ACTION_FAILED",
    "needs_review": "ACTION_FAILED",
    "rejected": "POLICY_EXCEPTION",
}
WHY = {
    "ACTION_FAILED": "The action did not complete or could not be verified on Shopify;"
    " needs human review.",
    "POLICY_EXCEPTION": "The brand did not approve the request, or it fell outside policy.",
}


async def _context(session: AsyncSession, conversation_id: uuid.UUID) -> tuple[str, str, str]:
    """(language, channel, brand) for rendering the outcome message on this conversation."""
    row = (
        await session.execute(
            text("SELECT language, channel FROM conversations WHERE id = :c"),
            {"c": conversation_id},
        )
    ).first()
    brand_row = (
        await session.execute(text("SELECT name FROM tenants WHERE id = app_current_tenant()"))
    ).first()
    parsed, _ = await policy_service.get_published_policy(session)
    reply_language = parsed.reply_language if isinstance(parsed, PolicyV2) else "en"
    lang = pick_language(row.language if row and row.language else "hinglish", reply_language)
    channel = row.channel if row else "whatsapp"
    brand = (brand_row.name if brand_row and brand_row.name else "") or "Support"
    return lang, channel, brand


def order_snapshot(final: Mapping[str, Any]) -> dict[str, Any] | None:
    """The verified order, as of the moment the customer was asked to confirm, kept in the
    conversation's context so the later outcome message (done / waiting for the team /
    could not complete) can show full order details without another Shopify call - that
    message is sent from the worker or the approvals API, which have no order lookup at
    hand. The customer's own phone/email are dropped; they are never part of a reply."""
    raw = (final.get("tool_results") or {}).get("order")
    if not raw:
        return None
    try:
        order = OrderFacts.model_validate(raw)
    except ValidationError:
        return None
    stripped = order.model_copy(update={"customer_phone": None, "customer_email": None})
    return stripped.model_dump(mode="json")


async def _snapshot_order(
    session: AsyncSession, conversation_id: uuid.UUID, order_ref: str
) -> OrderFacts | None:
    row = (
        await session.execute(
            text("SELECT context FROM conversation_state WHERE conversation_id = :c"),
            {"c": conversation_id},
        )
    ).first()
    raw = (row.context or {}).get("order_snapshot") if row else None
    if not raw:
        return None
    try:
        order = OrderFacts.model_validate(raw)
    except ValidationError:
        return None
    # A later request on another order overwrites the snapshot - never show the wrong one.
    return order if order.order_name == order_ref else None


def _address_text(address: Mapping[str, Any]) -> str:
    parts = [address.get("line1"), address.get("line2"), address.get("city"), address.get("state")]
    line = ", ".join(str(p) for p in parts if p)
    return f"{line} - {address.get('pin')}"


def outcome_facts(
    action: writes.ActionRow,
    lang: str,
    channel: str,
    brand: str,
    order: OrderFacts | None,
    status: str,
) -> dict[str, object]:
    facts: dict[str, object] = {"order": action.order_ref, "brand": brand}
    if action.tool == "refund_create":
        facts["amount"] = t.rs(action.amount_paise)
    elif action.tool == "address_update" and isinstance(action.args.get("new_address"), dict):
        facts["address"] = _address_text(action.args["new_address"])
    elif action.tool == "exchange_request":
        facts["item"] = str(action.args.get("line_item") or "")
        facts["variant"] = str(action.args.get("new_variant") or "")
    # include_status=False: payment/delivery come from the confirm-time snapshot and would
    # be stale next to "completed"; the request rows carry the real, current status.
    facts.update(
        t.detail_facts(channel, lang, action.tool, order, facts, status, include_status=False)
    )
    return facts


def done_text(
    action: writes.ActionRow,
    lang: str,
    channel: str,
    brand: str,
    order: OrderFacts | None = None,
) -> str:
    facts = outcome_facts(action, lang, channel, brand, order, "completed")
    return t.render(f"done_{action.tool}", lang, channel=channel, **facts)


def handoff_for(action: writes.ActionRow, reason: str, lang: str) -> DecisionV1:
    summary = HandoffSummaryV1(
        order_ref=action.order_ref,
        customer_asked=f"{action.tool} on {action.order_ref}"[:400],
        agent_did=[f"action {action.status}: {action.error or '-'}"[:200]],
        reason_code=reason,
        why_stuck=WHY.get(reason, "Needs human assistance."),
        language=lang,
    )
    return DecisionV1(
        action="handoff",
        reason_code=reason,
        handoff=summary,
        conv=ConvUpdate(clear_pending=True),
    )


async def notify(
    factory: Factory,
    senders: Senders,
    mailer: Mailer,
    tenant_id: uuid.UUID,
    action_id: uuid.UUID,
    mode: str = "auto",
) -> str:
    """Tell the customer what really happened to an action, once. Reads the stored status."""
    async with session_scope(factory, tenant_id=tenant_id) as session:
        action = await writes.get_action(session, action_id)
        if action is None:
            return "missing"
        lang, channel, brand = await _context(session, action.conversation_id)
        order = await _snapshot_order(session, action.conversation_id, action.order_ref)
    key = f"act-result:{action.id}:{action.status}"
    if action.status == "confirmed":
        body = done_text(action, lang, channel, brand, order)
    elif action.status == "pending_approval":
        facts = outcome_facts(action, lang, channel, brand, order, "review")
        body = t.render("awaiting_team", lang, channel=channel, **facts)
    elif action.status in HANDOFF_FOR:
        reason = HANDOFF_FOR[action.status]
        decision = handoff_for(action, reason, lang)
        facts = outcome_facts(action, lang, channel, brand, order, "review")
        ack = t.render("action_failed_ack", lang, channel=channel, **facts)
        await engine.submit_reply(
            factory,
            senders,
            tenant_id=tenant_id,
            conversation_id=action.conversation_id,
            run_id=None,
            body=ack,
            key=key,
            mode=mode,
        )
        await handoffs.create_handoff(
            factory,
            mailer,
            senders,
            tenant_id=tenant_id,
            conversation_id=action.conversation_id,
            run_id=None,
            decision=decision,
            mode=mode,
        )
        return "handoff"
    else:
        return action.status
    await engine.submit_reply(
        factory,
        senders,
        tenant_id=tenant_id,
        conversation_id=action.conversation_id,
        run_id=None,
        body=body,
        key=key,
        mode=mode,
    )
    return "replied"
