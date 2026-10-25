import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.senders import Senders
from app.contracts.contracts import DecisionOut
from app.core import timeutil
from app.core.db import session_scope
from app.core.mail import Mailer, send_safely
from app.v1.contracts import DecisionV1
from app.v1.policy import AnyPolicy, parse_any

logger = logging.getLogger("app.handoffs")
Factory = async_sessionmaker[AsyncSession]


def sla_due(policy: AnyPolicy | None, tz: str, now: Any) -> Any:
    minutes = policy.handoff_sla_minutes if policy else 30
    if policy is None:
        return now + timedelta(minutes=minutes)
    hours = policy.office_hours
    opens = timeutil.next_open_utc(now, hours.start, hours.end, hours.days, tz)
    base = opens or now
    return base + timedelta(minutes=minutes)


async def create_handoff(
    factory: Factory,
    mailer: Mailer,
    senders: Senders,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID | None,
    decision: DecisionOut | DecisionV1,
    mode: str,
) -> uuid.UUID | None:
    if decision.handoff is None:
        raise ValueError("handoff decision without summary")
    shadow = mode == "shadow"
    now = timeutil.utc_now()
    async with session_scope(factory, tenant_id=tenant_id) as session:
        policy_row = (
            await session.execute(text("SELECT content FROM policies WHERE status = 'published'"))
        ).first()
        settings = (
            await session.execute(
                text(
                    "SELECT notify_email_enabled, notify_email, notify_whatsapp_enabled, alert_phone,"
                    " timezone FROM tenant_settings"
                )
            )
        ).first()
        policy = parse_any(policy_row.content) if policy_row else None
        tz = settings.timezone if settings else "Asia/Kolkata"
        due = sla_due(policy, tz, now)
        row = (
            await session.execute(
                text(
                    "INSERT INTO handoffs (conversation_id, run_id, reason_code, priority, summary,"
                    " would_handoff, status, sla_due_at, resolved_at) VALUES (:c, :r, :rc, :p,"
                    " CAST(:s AS jsonb), :w, :st, :due, :ra)"
                    " ON CONFLICT (tenant_id, conversation_id) WHERE status IN ('open','claimed')"
                    " DO NOTHING RETURNING id"
                ),
                {
                    "c": conversation_id,
                    "r": run_id,
                    "rc": decision.reason_code,
                    "p": decision.priority,
                    "s": decision.handoff.model_dump_json(),
                    "w": shadow,
                    "st": "resolved" if shadow else "open",
                    "due": None if shadow else due,
                    "ra": now if shadow else None,
                },
            )
        ).first()
        if row is None:
            return None
        handoff_id: uuid.UUID = row.id
        if shadow:
            return handoff_id
        await session.execute(
            text("UPDATE conversations SET status = 'handoff' WHERE id = :c"),
            {"c": conversation_id},
        )
        title = f"Handoff: {decision.reason_code}"
        await session.execute(
            text(
                "INSERT INTO notifications (audience, title, body, link) VALUES ('admin', :t, :b, :l)"
            ),
            {
                "t": title,
                "b": decision.handoff.why_stuck,
                "l": f"/handoffs/{handoff_id}",
            },
        )
    await _alert(mailer, senders, settings, decision, handoff_id)
    return handoff_id


async def _alert(
    mailer: Mailer,
    senders: Senders,
    settings: Any,
    decision: DecisionOut | DecisionV1,
    handoff_id: uuid.UUID,
) -> None:
    if settings is None or decision.handoff is None:
        return
    summary = decision.handoff
    lines = [
        f"Reason: {decision.reason_code} ({decision.priority})",
        f"Customer: {summary.customer_masked_phone or '-'}",
        f"Order: {summary.order_ref or '-'}",
        f"Customer ne kaha: {summary.customer_asked}",
        f"Kyun atka: {summary.why_stuck}",
        f"Handoff id: {handoff_id}",
    ]
    if settings.notify_email_enabled and settings.notify_email:
        await send_safely(
            mailer,
            settings.notify_email,
            f"[Support] Handoff: {decision.reason_code}",
            "\n".join(lines),
        )
    if settings.notify_whatsapp_enabled and settings.alert_phone and senders.whatsapp is not None:
        try:
            await senders.whatsapp.send_text(
                settings.alert_phone,
                f"Handoff {decision.reason_code} ({decision.priority}): {summary.why_stuck}",
            )
        except Exception as exc:
            logger.error("whatsapp alert failed", extra={"error_class": type(exc).__name__})


async def escalate_urgent(
    factory: Factory,
    mailer: Mailer,
    senders: Senders,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    title: str,
    body: str,
) -> bool:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        handoff_id = (
            await session.execute(
                text(
                    "UPDATE handoffs SET priority = 'urgent' WHERE conversation_id = :c"
                    " AND status IN ('open','claimed') RETURNING id"
                ),
                {"c": conversation_id},
            )
        ).scalar_one_or_none()
        await session.execute(
            text(
                "INSERT INTO notifications (audience, title, body, link) VALUES ('admin', :t, :b, :l)"
            ),
            {
                "t": title,
                "b": body,
                "l": f"/handoffs/{handoff_id}" if handoff_id else "/handoffs",
            },
        )
        settings = (
            await session.execute(
                text(
                    "SELECT notify_email_enabled, notify_email, notify_whatsapp_enabled, alert_phone"
                    " FROM tenant_settings"
                )
            )
        ).first()
    if settings is not None:
        if settings.notify_email_enabled and settings.notify_email:
            await send_safely(mailer, settings.notify_email, f"[Support] {title}", body)
        if (
            settings.notify_whatsapp_enabled
            and settings.alert_phone
            and senders.whatsapp is not None
        ):
            try:
                await senders.whatsapp.send_text(settings.alert_phone, f"{title}: {body}")
            except Exception as exc:
                logger.error("whatsapp alert failed", extra={"error_class": type(exc).__name__})
    return handoff_id is not None
