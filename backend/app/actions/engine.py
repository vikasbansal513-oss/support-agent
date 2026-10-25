import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.senders import Senders
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError
from app.core.flags import is_globally_paused
from app.integrations.gmail import GmailAuthError, GmailUpstreamError
from app.integrations.gupshup import SendError
from app.v1.policy import parse_any

logger = logging.getLogger("app.actions")
Factory = async_sessionmaker[AsyncSession]
FINAL = frozenset({"sent", "shadow", "draft", "rejected", "suppressed", "needs_review"})
MAX_RECLAIMS = 3
WINDOW_HOURS = 24
KNOWN_NOT_SENT = frozenset({"auth_error", "rejected", "not_configured"})
PHONE_CHANNELS = ("whatsapp", "voice")


class ActionDeferred(Exception):
    pass


@dataclass(frozen=True)
class ActionResult:
    action_id: uuid.UUID | None
    status: str
    detail: str | None = None
    already_handled: bool = False


def _known_not_sent(kind: str) -> bool:
    return kind in KNOWN_NOT_SENT or kind.startswith("http_4")


async def _recipient(session: AsyncSession, conversation_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT c.channel, c.customer_id, c.last_inbound_at, cu.phone_e164, cu.email_lower"
                " FROM conversations c JOIN customers cu ON cu.id = c.customer_id"
                " WHERE c.id = :c"
            ),
            {"c": conversation_id},
        )
    ).one()


async def _insert_out_message(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    channel: str,
    body: str,
    action_id: uuid.UUID,
    sender: str,
    external_id: str | None = None,
) -> None:
    exists = (
        await session.execute(text("SELECT 1 FROM messages WHERE action_id = :a"), {"a": action_id})
    ).first()
    if exists is not None:
        return
    await session.execute(
        text(
            "INSERT INTO messages (conversation_id, direction, sender, channel, content,"
            " external_id, action_id) VALUES (:c, 'out', :s, :ch, :body, :ext, :a)"
        ),
        {
            "c": conversation_id,
            "s": sender,
            "ch": channel,
            "body": body,
            "ext": external_id,
            "a": action_id,
        },
    )
    await session.execute(
        text("UPDATE conversations SET last_message_at = now() WHERE id = :c"),
        {"c": conversation_id},
    )


async def submit_reply(
    factory: Factory,
    senders: Senders,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID | None,
    body: str,
    key: str,
    mode: str,
    sender_role: str = "agent",
    decided_by: uuid.UUID | None = None,
    human: bool = False,
) -> ActionResult:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        target = await _recipient(session, conversation_id)
        recipient = target.phone_e164 if target.channel in PHONE_CHANNELS else target.email_lower
        initial = {"shadow": "shadow", "draft": "draft"}.get(mode, "claimed")
        row = (
            await session.execute(
                text(
                    "INSERT INTO agent_actions (run_id, conversation_id, action_type, idempotency_key,"
                    " status, recipient, body_text, attempts, decided_by, decided_at)"
                    " VALUES (:r, :c, 'reply_customer', :k, :s, :to, :b, :att, :by, :at)"
                    " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id"
                ),
                {
                    "r": run_id,
                    "c": conversation_id,
                    "k": key,
                    "s": initial,
                    "to": recipient,
                    "b": body,
                    "att": 1 if initial == "claimed" else 0,
                    "by": decided_by,
                    "at": timeutil.utc_now() if decided_by else None,
                },
            )
        ).first()
        if row is None:
            existing = await _existing(session, key)
            if existing.status != "reclaimed" or existing.action_id is None:
                return existing
            action_id = existing.action_id
            initial = "claimed"
        else:
            action_id = row.id
        if initial != "claimed":
            await _insert_out_message(
                session, conversation_id, target.channel, body, action_id, sender_role
            )
            return ActionResult(action_id, initial)
    return await _deliver(factory, senders, tenant_id, action_id, sender_role, human)


async def _existing(session: AsyncSession, key: str) -> ActionResult:
    current = (
        await session.execute(
            text(
                "SELECT id, status, attempts, claimed_at FROM agent_actions"
                " WHERE idempotency_key = :k FOR UPDATE"
            ),
            {"k": key},
        )
    ).one()
    if current.status in FINAL:
        return ActionResult(current.id, current.status, already_handled=True)
    if current.status == "failed" and current.attempts < MAX_RECLAIMS:
        await session.execute(
            text(
                "UPDATE agent_actions SET status = 'claimed', attempts = attempts + 1,"
                " claimed_at = now(), error = NULL WHERE id = :i"
            ),
            {"i": current.id},
        )
        return ActionResult(current.id, "reclaimed")
    stale_after = timedelta(minutes=get_settings().action_stale_minutes)
    if current.status == "claimed" and timeutil.utc_now() - current.claimed_at > stale_after:
        await session.execute(
            text(
                "UPDATE agent_actions SET status = 'needs_review', error = 'STALE_CLAIM' WHERE id = :i"
            ),
            {"i": current.id},
        )
        return ActionResult(current.id, "needs_review", "STALE_CLAIM", already_handled=True)
    return ActionResult(current.id, current.status, "in_progress", already_handled=True)


async def _day_counts(
    session: AsyncSession, conversation_id: uuid.UUID, now: datetime
) -> tuple[int, int]:
    start = timeutil.to_ist(now).replace(hour=0, minute=0, second=0, microsecond=0)
    row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE conversation_id IN (SELECT id FROM conversations"
                " WHERE customer_id = (SELECT customer_id FROM conversations WHERE id = :c))) AS cust,"
                " count(*) AS brand FROM agent_actions WHERE action_type = 'reply_customer'"
                " AND status IN ('sent','claimed') AND created_at >= :s"
            ),
            {"c": conversation_id, "s": start},
        )
    ).one()
    return int(row.cust), int(row.brand)


async def guard_reasons(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    now: datetime,
    human: bool = False,
    recipient: str | None = None,
) -> str | None:
    target = await _recipient(session, conversation_id)
    expected = target.phone_e164 if target.channel in PHONE_CHANNELS else target.email_lower
    if recipient is not None and recipient != expected:
        return "RECIPIENT_MISMATCH"
    paused = (
        await session.execute(
            text("SELECT agent_paused FROM tenants WHERE id = app_current_tenant()")
        )
    ).scalar_one()
    if (paused or await is_globally_paused(session)) and not human:
        return "AGENT_PAUSED"
    opted = (
        await session.execute(
            text("SELECT 1 FROM opt_outs WHERE customer_id = :c AND channel = :ch"),
            {"c": target.customer_id, "ch": target.channel},
        )
    ).first()
    if opted is not None:
        return "OPTED_OUT"
    if target.channel == "whatsapp" and (
        target.last_inbound_at is None
        or now - target.last_inbound_at > timedelta(hours=WINDOW_HOURS)
    ):
        return "WINDOW_CLOSED"
    policy_row = (
        await session.execute(text("SELECT content FROM policies WHERE status = 'published'"))
    ).first()
    if policy_row is not None and not human:
        policy = parse_any(policy_row.content)
        cust, brand = await _day_counts(session, conversation_id, now)
        if cust > policy.customer_daily_msg_cap or brand > policy.brand_daily_msg_cap:
            return "CAP_REACHED"
    return None


async def _email_context(
    session: AsyncSession, conversation_id: uuid.UUID
) -> dict[str, str | None]:
    row = (
        await session.execute(
            text(
                "SELECT e.raw_payload FROM messages m JOIN inbound_events e ON e.id = m.inbound_event_id"
                " WHERE m.conversation_id = :c AND m.direction = 'in' ORDER BY m.created_at DESC LIMIT 1"
            ),
            {"c": conversation_id},
        )
    ).first()
    normalized = (row.raw_payload or {}).get("normalized", {}) if row else {}
    headers = {k.lower(): v for k, v in (normalized.get("email_headers") or {}).items()}
    return {
        "thread_id": normalized.get("email_thread_id"),
        "subject": normalized.get("email_subject") or "Aapka sawaal",
        "in_reply_to": headers.get("message-id"),
    }


async def _deliver(
    factory: Factory,
    senders: Senders,
    tenant_id: uuid.UUID,
    action_id: uuid.UUID,
    sender_role: str = "agent",
    human: bool = False,
) -> ActionResult:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        action = (
            await session.execute(
                text(
                    "SELECT id, conversation_id, recipient, body_text FROM agent_actions WHERE id = :i"
                ),
                {"i": action_id},
            )
        ).one()
        reason = await guard_reasons(
            session, action.conversation_id, timeutil.utc_now(), human, action.recipient
        )
        target = await _recipient(session, action.conversation_id)
        email_ctx = (
            await _email_context(session, action.conversation_id)
            if target.channel == "email"
            else {}
        )
        if reason is None and target.channel == "voice" and senders.voice is None:
            reason = "VOICE_CALL_ENDED"
            await session.execute(
                text(
                    "INSERT INTO notifications (audience, title, body) VALUES"
                    " ('admin', 'Customer ko call karke batayein', :b)"
                ),
                {
                    "b": f"Call khatam ho chuki hai. Is baat ka update customer ko phone par dein: {action.body_text[:300]}"
                },
            )
        if reason:
            await session.execute(
                text("UPDATE agent_actions SET status = 'suppressed', error = :e WHERE id = :i"),
                {"e": reason, "i": action_id},
            )
            return ActionResult(action_id, "suppressed", reason)
    try:
        external = await _send(
            senders, target.channel, action.recipient, action.body_text, email_ctx
        )
    except (SendError, GmailAuthError, GmailUpstreamError) as exc:
        kind = getattr(exc, "kind", None) or (
            "auth_error" if isinstance(exc, GmailAuthError) else str(exc)
        )
        if _known_not_sent(kind):
            async with session_scope(factory, tenant_id=tenant_id) as session:
                await session.execute(
                    text("UPDATE agent_actions SET status = 'failed', error = :e WHERE id = :i"),
                    {"e": kind, "i": action_id},
                )
            return ActionResult(action_id, "failed", kind)
        logger.error("send outcome unknown", extra={"error_class": kind})
        raise ActionDeferred(kind) from exc
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_actions SET status = 'sent', external_ref = :x, sent_at = now()"
                " WHERE id = :i"
            ),
            {"x": external, "i": action_id},
        )
        await _insert_out_message(
            session,
            action.conversation_id,
            target.channel,
            action.body_text,
            action_id,
            sender_role,
            external,
        )
    return ActionResult(action_id, "sent")


async def _send(
    senders: Senders, channel: str, recipient: str, body: str, email_ctx: dict[str, str | None]
) -> str:
    if channel == "voice":
        if senders.voice is None:
            raise SendError("not_configured")
        return await senders.voice.send_text(recipient, body)
    if channel == "whatsapp":
        if senders.whatsapp is None:
            raise SendError("not_configured")
        return await senders.whatsapp.send_text(recipient, body)
    if senders.email is None or not senders.email_address:
        raise SendError("not_configured")
    return await senders.email.send_reply(
        sender=senders.email_address,
        to=recipient,
        subject=str(email_ctx.get("subject") or ""),
        body=body,
        thread_id=email_ctx.get("thread_id"),
        in_reply_to=email_ctx.get("in_reply_to"),
    )


async def record_tool_calls(
    session: AsyncSession,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    calls: list[dict[str, Any]],
) -> None:
    for index, call in enumerate(calls, start=1):
        await session.execute(
            text(
                "INSERT INTO agent_actions (run_id, conversation_id, action_type, idempotency_key,"
                " status, tool_name, tool_args, tool_result, sent_at) VALUES (:r, :c, 'tool_call',"
                " :k, :s, :t, CAST(:a AS jsonb), CAST(:res AS jsonb), now())"
                " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
            ),
            {
                "r": run_id,
                "c": conversation_id,
                "k": f"{run_id}:tool:{index}",
                "s": "sent" if call["ok"] else "failed",
                "t": call["tool"],
                "a": json.dumps(call.get("args", {})),
                "res": json.dumps({"ok": call["ok"], "error": call.get("error")}),
            },
        )


async def approve(
    factory: Factory,
    senders: Senders,
    tenant_id: uuid.UUID,
    action_id: uuid.UUID,
    decided_by: uuid.UUID,
) -> ActionResult:
    async with session_scope(factory, tenant_id=tenant_id, user_id=decided_by) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status FROM agent_actions WHERE id = :i AND action_type = 'reply_customer' FOR UPDATE"
                ),
                {"i": action_id},
            )
        ).first()
        if row is None:
            raise AppError("E-NF-001")
        if row.status != "draft":
            raise AppError("E-ACT-001")
        await session.execute(
            text(
                "UPDATE agent_actions SET status = 'claimed', attempts = attempts + 1, claimed_at = now(),"
                " decided_by = :u, decided_at = now() WHERE id = :i"
            ),
            {"u": decided_by, "i": action_id},
        )
        await record(
            session,
            AuditEvent(
                action="action.approved",
                entity_type="agent_action",
                entity_id=str(action_id),
                actor_user_id=decided_by,
            ),
        )
    try:
        result = await _deliver(factory, senders, tenant_id, action_id)
    except ActionDeferred as exc:
        raise AppError("E-ACT-004", detail=str(exc)) from exc
    raise_for(result)
    return result


def raise_for(result: ActionResult) -> None:
    if result.status == "suppressed":
        mapping = {"WINDOW_CLOSED": "E-ACT-002", "OPTED_OUT": "E-ACT-003"}
        raise AppError(mapping.get(result.detail or "", "E-ACT-001"), detail=result.detail)
    if result.status == "failed":
        raise AppError("E-ACT-004", detail=result.detail)


async def reject(
    factory: Factory, tenant_id: uuid.UUID, action_id: uuid.UUID, decided_by: uuid.UUID
) -> ActionResult:
    async with session_scope(factory, tenant_id=tenant_id, user_id=decided_by) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status FROM agent_actions WHERE id = :i AND action_type = 'reply_customer' FOR UPDATE"
                ),
                {"i": action_id},
            )
        ).first()
        if row is None:
            raise AppError("E-NF-001")
        if row.status != "draft":
            raise AppError("E-ACT-001")
        await session.execute(
            text(
                "UPDATE agent_actions SET status = 'rejected', decided_by = :u, decided_at = now() WHERE id = :i"
            ),
            {"u": decided_by, "i": action_id},
        )
        await record(
            session,
            AuditEvent(
                action="action.rejected",
                entity_type="agent_action",
                entity_id=str(action_id),
                actor_user_id=decided_by,
            ),
        )
    return ActionResult(action_id, "rejected")
