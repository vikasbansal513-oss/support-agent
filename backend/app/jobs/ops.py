import json
import logging
import math
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions import writes
from app.actions.senders import build_senders
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.mail import get_mailer, send_safely
from app.integrations.shopify_write import ShopifyWriter, get_writer_for_tenant
from app.jobs.handlers import Handler, JobContext
from app.policy import service as policy_service
from app.privacy import service as privacy
from app.v1 import outcome
from app.v1.policy import PolicyV2

logger = logging.getLogger("app.jobs.ops")


async def notify_once(
    session: AsyncSession, title: str, body: str, link: str | None, window_hours: int = 12
) -> bool:
    since = timeutil.utc_now() - timedelta(hours=window_hours)
    exists = (
        await session.execute(
            text("SELECT 1 FROM notifications WHERE title = :t AND created_at >= :s LIMIT 1"),
            {"t": title, "s": since},
        )
    ).first()
    if exists is not None:
        return False
    await session.execute(
        text(
            "INSERT INTO notifications (audience, title, body, link) VALUES ('admin', :t, :b, :l)"
        ),
        {"t": title, "b": body, "l": link},
    )
    return True


async def sla_check(ctx: JobContext) -> dict[str, Any]:
    now = timeutil.utc_now()
    alerted = 0
    reminder_to: dict[str, Any] | None = None
    async with session_scope(ctx.factory, tenant_id=ctx.job.tenant_id) as session:
        policy, _ = await policy_service.get_published_policy(session)
        reminder = int(getattr(policy, "handoff_reminder_minutes", 0) or 0)
        again = now - timedelta(minutes=reminder or get_settings().sla_realert_minutes)
        rows = (
            await session.execute(
                text(
                    "SELECT id, conversation_id FROM handoffs WHERE status IN ('open','claimed')"
                    " AND would_handoff = false AND sla_due_at IS NOT NULL AND sla_due_at < :n"
                    " AND (last_alert_at IS NULL OR last_alert_at < :a) FOR UPDATE"
                ),
                {"n": now, "a": again},
            )
        ).all()
        for row in rows:
            await session.execute(
                text(
                    "UPDATE handoffs SET alert_count = alert_count + 1, last_alert_at = :n"
                    " WHERE id = :i"
                ),
                {"n": now, "i": row.id},
            )
            await session.execute(
                text(
                    "INSERT INTO notifications (audience, title, body, link)"
                    " VALUES ('admin', 'Handoff SLA nikal gaya', 'Customer abhi bhi intezaar me hai', :l)"
                ),
                {"l": f"/handoffs/{row.id}"},
            )
            await record(
                session,
                AuditEvent(
                    action="handoff.sla_realert",
                    entity_type="handoff",
                    entity_id=str(row.id),
                    actor_role="system",
                ),
            )
            alerted += 1
        if alerted and reminder > 0:
            contact = (
                await session.execute(
                    text(
                        "SELECT notify_email_enabled, notify_email, notify_whatsapp_enabled,"
                        " alert_phone FROM tenant_settings"
                    )
                )
            ).first()
            if contact is not None:
                reminder_to = {
                    "email": contact.notify_email
                    if contact.notify_email_enabled and contact.notify_email
                    else None,
                    "phone": contact.alert_phone
                    if contact.notify_whatsapp_enabled and contact.alert_phone
                    else None,
                }
    if reminder_to is not None:
        await _send_sla_reminder(ctx, reminder_to, alerted)
    return {"alerted": alerted}


async def _send_sla_reminder(ctx: JobContext, to: dict[str, Any], count: int) -> None:
    message = f"{count} handoff(s) are still waiting for your team, past the reply time."
    if to["email"]:
        await send_safely(get_mailer(), to["email"], "[Support] Handoff reminder", message)
    if to["phone"]:
        senders = ctx.senders or await build_senders(ctx.factory, ctx.job.tenant_id, ctx.transport)
        if senders.whatsapp is not None:
            try:
                await senders.whatsapp.send_text(to["phone"], f"Handoff reminder: {message}")
            except Exception as exc:
                logger.error("whatsapp reminder failed", extra={"error_class": type(exc).__name__})


def p95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def llm_latency_check(ctx: JobContext) -> dict[str, Any]:
    settings = get_settings()
    since = timeutil.utc_now() - timedelta(minutes=15)
    async with session_scope(ctx.factory, tenant_id=ctx.job.tenant_id) as session:
        values = [
            int(v)
            for v in (
                await session.execute(
                    text(
                        "SELECT latency_ms FROM llm_calls WHERE outcome = 'ok' AND created_at >= :s"
                    ),
                    {"s": since},
                )
            )
            .scalars()
            .all()
        ]
        if len(values) < 5:
            return {"checked": len(values), "alert": False}
        value = p95(values)
        alert = value > settings.llm_p95_target_ms
        if alert:
            await notify_once(
                session,
                "LLM dheere chal raha hai",
                f"p95 {value} ms, target {settings.llm_p95_target_ms} ms",
                "/admin/ops",
                1,
            )
    return {"checked": len(values), "p95_ms": value, "alert": alert}


async def unanswered_alert(ctx: JobContext) -> dict[str, Any]:
    minutes = get_settings().unanswered_alert_minutes
    cutoff = timeutil.utc_now() - timedelta(minutes=minutes)
    async with session_scope(ctx.factory, tenant_id=ctx.job.tenant_id) as session:
        count = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM jobs WHERE kind = 'process_inbound'"
                        " AND status IN ('deferred','needs_review','failed') AND created_at < :c"
                    ),
                    {"c": cutoff},
                )
            ).scalar_one()
        )
        if count > 0:
            await notify_once(
                session,
                "Kuch customers ko jawab nahi gaya",
                f"{count} message(s) atke hain (LLM ya processing fail)",
                "/admin/ops",
                1,
            )
    return {"unanswered": count}


async def privacy_due(ctx: JobContext) -> dict[str, Any]:
    warn = timeutil.utc_now() + timedelta(days=get_settings().privacy_warn_days)
    async with session_scope(ctx.factory, tenant_id=ctx.job.tenant_id) as session:
        count = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM privacy_requests WHERE status = 'open' AND due_at < :w"
                    ),
                    {"w": warn},
                )
            ).scalar_one()
        )
        if count > 0:
            await notify_once(
                session,
                "Privacy request ki deadline paas hai",
                f"{count} request(s) 30 din ki limit ke paas ya paar",
                "/privacy",
                20,
            )
    return {"due": count}


async def retention(ctx: JobContext) -> dict[str, Any]:
    deleted = await privacy.execute_delete_requests(ctx.factory, ctx.job.tenant_id)
    result = await privacy.run_retention(ctx.factory, ctx.job.tenant_id)
    logger.info("retention done", extra={"summary": json.dumps(result)})
    return {"privacy_deleted": deleted, **result}


async def write_upkeep(ctx: JobContext) -> dict[str, Any]:
    expired = await writes.expire_stale(ctx.factory, ctx.job.tenant_id)
    async with session_scope(ctx.factory, tenant_id=ctx.job.tenant_id) as session:
        ids = list(
            (
                await session.execute(
                    text("SELECT id FROM agent_actions WHERE status = 'needs_review' LIMIT 50")
                )
            )
            .scalars()
            .all()
        )
    writer = await get_writer_for_tenant(ctx.factory, ctx.job.tenant_id)
    stuck = await _finish_stuck(ctx, writer)
    reconciled = 0
    for action_id in ids:
        outcome = await writes.reconcile(ctx.factory, writer, ctx.job.tenant_id, action_id)
        reconciled += outcome.status == "confirmed"
    return {"expired": expired, "reconciled": reconciled, "resumed": stuck}


async def _finish_stuck(ctx: JobContext, writer: ShopifyWriter) -> int:
    tenant_id = ctx.job.tenant_id
    async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
        ids = list(
            (
                await session.execute(
                    text(
                        "SELECT id FROM agent_actions WHERE status = 'approved'"
                        " AND decided_at < now() - interval '2 minutes' LIMIT 20"
                    )
                )
            )
            .scalars()
            .all()
        )
        policy, _ = await policy_service.get_published_policy(session)
        mode = (
            await session.execute(text("SELECT mode FROM tenants WHERE id = :t"), {"t": tenant_id})
        ).scalar_one()
    if not ids or not isinstance(policy, PolicyV2):
        return 0
    senders = await build_senders(ctx.factory, tenant_id, ctx.transport)
    for action_id in ids:
        await writes.execute(ctx.factory, writer, tenant_id, action_id, policy, enforce_limits=True)
        await outcome.notify(ctx.factory, senders, get_mailer(), tenant_id, action_id, str(mode))
    return len(ids)


async def approval_sla(ctx: JobContext) -> dict[str, Any]:
    limit = timeutil.utc_now() - timedelta(minutes=get_settings().approval_sla_minutes)
    async with session_scope(ctx.factory, tenant_id=ctx.job.tenant_id) as session:
        count = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_actions WHERE status = 'pending_approval'"
                        " AND created_at < :l"
                    ),
                    {"l": limit},
                )
            ).scalar_one()
        )
        if count > 0:
            await notify_once(
                session,
                "Approvals ruki hui hain",
                f"{count} request(s) approval ke intezaar me SLA se zyada der se hain",
                "/approvals",
                4,
            )
    return {"overdue": count}


HANDLERS: dict[str, Handler] = {
    "approval_sla": approval_sla,
    "write_upkeep": write_upkeep,
    "sla_check": sla_check,
    "llm_latency_check": llm_latency_check,
    "unanswered_alert": unanswered_alert,
    "privacy_due": privacy_due,
    "retention": retention,
}
