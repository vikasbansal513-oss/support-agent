import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine, writes
from app.actions.senders import Senders
from app.agent.validate import pick_language
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
    "ACTION_FAILED": "Action Shopify par poora ya verify nahi hua, insaan check kare.",
    "POLICY_EXCEPTION": "Brand ne request approve nahi ki ya policy ke bahar thi.",
}


async def _language(session: AsyncSession, conversation_id: uuid.UUID) -> str:
    row = (
        await session.execute(
            text("SELECT language FROM conversations WHERE id = :c"), {"c": conversation_id}
        )
    ).first()
    parsed, _ = await policy_service.get_published_policy(session)
    reply_language = parsed.reply_language if isinstance(parsed, PolicyV2) else "en"
    return pick_language(row.language if row and row.language else "hinglish", reply_language)


def done_text(action: writes.ActionRow, lang: str) -> str:
    facts: dict[str, object] = {"order": action.order_ref}
    if action.tool == "refund_create":
        facts["amount"] = t.rs(action.amount_paise)
    return t.render(f"done_{action.tool}", lang, **facts)


def handoff_for(action: writes.ActionRow, reason: str, lang: str) -> DecisionV1:
    summary = HandoffSummaryV1(
        order_ref=action.order_ref,
        customer_asked=f"{action.tool} on {action.order_ref}"[:400],
        agent_did=[f"action {action.status}: {action.error or '-'}"[:200]],
        reason_code=reason,
        why_stuck=WHY.get(reason, "Insaan ki madad chahiye."),
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
        lang = await _language(session, action.conversation_id)
    key = f"act-result:{action.id}:{action.status}"
    if action.status == "confirmed":
        body = done_text(action, lang)
    elif action.status == "pending_approval":
        body = t.render("awaiting_team", lang)
    elif action.status in HANDOFF_FOR:
        reason = HANDOFF_FOR[action.status]
        decision = handoff_for(action, reason, lang)
        ack = (
            "Aapki request abhi poori nahi ho payi. Hamari team ise dekhkar aapse sampark karegi."
            if lang != "en"
            else "We could not complete your request yet. Our team will review it and get back to you."
        )
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
