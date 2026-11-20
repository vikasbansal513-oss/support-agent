import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.senders import Senders
from app.contracts.contracts import DecisionOut
from app.core import officehours, timeutil
from app.core.db import session_scope
from app.core.mail import Mailer, send_safely
from app.policy import service as policy_service
from app.v1.contracts import DecisionV1
from app.v1.policy import AnyPolicy

logger = logging.getLogger("app.handoffs")

# ROOT-CAUSE FIX (2026-11-20): a real live voice call went silent for ~19s between
# the customer finishing speaking and them hanging up. Log timing showed a ~4.7s
# gap inside app.agent.runner_v1's "action" step - between "load_row done" and
# "action done" - with nothing else happening in between except this module's
# create_handoff()/_alert() being awaited. _alert() calls send_safely() (a real
# Brevo HTTP email API call, app.core.mail.BrevoMailer, timeout=10s) and, if
# WhatsApp alerts are on, senders.whatsapp.send_text() too - both real network
# round trips to notify STAFF, not the customer. create_handoff() awaited _alert()
# to finish before returning, so app.agent.runner_v1._handoff() could not compose
# or send the CUSTOMER's own reply (the handoff reference-number message) until
# the staff notification email/WhatsApp had already gone out - on a live phone
# call, the caller was made to sit in silence for someone else's email to send.
# Keeping a module-level set of the fire-and-forget tasks is required, not
# cosmetic: asyncio only holds a weak reference to a task object, and a task with
# no strong reference anywhere can be garbage-collected mid-flight, silently
# cancelling the alert before it sends (see asyncio.create_task's own docs on
# this). _alert_in_background wraps the real work in its own try/except so a
# background failure is logged, not swallowed into an unhandled "Task exception
# was never retrieved" warning. VERIFY: not exercised against a live provider
# from here (no app execution/pytest per this change's own constraints) - only
# checked with ast.parse/ruff. Confirm on the next live handoff that the
# customer's reference-number reply now goes out immediately and that the staff
# email/WhatsApp alert still arrives moments later in the logs/inbox.
_background_tasks: set[asyncio.Task[None]] = set()


async def _alert_in_background(
    mailer: Mailer,
    senders: Senders,
    settings: Any,
    decision: DecisionOut | DecisionV1,
    handoff_id: uuid.UUID,
) -> None:
    try:
        await _alert(mailer, senders, settings, decision, handoff_id)
    except Exception as exc:
        logger.error("handoff alert failed", extra={"error_class": type(exc).__name__})
Factory = async_sessionmaker[AsyncSession]


def reference_code(handoff_id: uuid.UUID) -> str:
    """Decision Framework Sections 3/4/8/9/15: every complaint, investigation, cancellation
    or escalation handed to a human must give the customer a reference/ticket number they
    can quote back. Derived straight from the handoff row's own id - no new column, no
    extra DB round trip - 8 uppercase hex characters so it reads cleanly over WhatsApp,
    email or a voice call."""
    return f"SA-{handoff_id.hex[:8].upper()}"


def sla_due(policy: AnyPolicy | None, tz: str, now: Any) -> Any:
    minutes = policy.handoff_sla_minutes if policy else 30
    if policy is None:
        return now + timedelta(minutes=minutes)
    opens = officehours.next_open(policy, now, tz)
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
        settings = (
            await session.execute(
                text(
                    "SELECT notify_email_enabled, notify_email, notify_whatsapp_enabled, alert_phone,"
                    " timezone FROM tenant_settings"
                )
            )
        ).first()
        policy, _ = await policy_service.get_published_policy(session)
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
    # ROOT-CAUSE FIX (2026-11-20): see the module-level comment above
    # _background_tasks - this used to be `await _alert(...)`, which blocked the
    # customer's own reply (sent later in app.agent.runner_v1._handoff(), after
    # create_handoff() returns) on a staff notification email/WhatsApp send.
    task = asyncio.create_task(_alert_in_background(mailer, senders, settings, decision, handoff_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
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
        # ROOT-CAUSE FIX (2026-11-20): same issue as create_handoff()/_alert() above
        # (see the module-level comment on _background_tasks) - this function is
        # called directly from app.voice.service on a live call (twice, for failed
        # transfers), so the same synchronous email/WhatsApp send here blocked the
        # live call on a staff notification exactly like the handoff path did.
        task = asyncio.create_task(_send_urgent_alert_in_background(mailer, senders, settings, title, body))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return handoff_id is not None


async def _send_urgent_alert_in_background(
    mailer: Mailer, senders: Senders, settings: Any, title: str, body: str
) -> None:
    try:
        if settings.notify_email_enabled and settings.notify_email:
            await send_safely(mailer, settings.notify_email, f"[Support] {title}", body)
        if (
            settings.notify_whatsapp_enabled
            and settings.alert_phone
            and senders.whatsapp is not None
        ):
            await senders.whatsapp.send_text(settings.alert_phone, f"{title}: {body}")
    except Exception as exc:
        logger.error("urgent alert failed", extra={"error_class": type(exc).__name__})
