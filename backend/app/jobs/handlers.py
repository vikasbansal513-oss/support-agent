import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.senders import Senders
from app.agent import keywords
from app.channels import email_classify, identity, normalize, prechecks
from app.contracts.contracts import EmailClassification, IntegrationKind, NormalizedInbound
from app.core import timeutil
from app.core.config import get_settings
from app.core.db import session_scope
from app.integrations import service as integrations
from app.integrations.gmail import GmailAuthError, GmailClient, GmailUpstreamError, HttpGmailClient
from app.integrations.http import DbCallRecorder, ExternalHttp
from app.jobs import queue
from app.policy import service as policy_service
from app.privacy import service as privacy
from app.v1.policy import PolicyV2

logger = logging.getLogger("app.jobs.handlers")
Factory = async_sessionmaker[AsyncSession]


@dataclass
class JobContext:
    factory: Factory
    job: queue.Job
    transport: httpx.AsyncBaseTransport | None = None
    gmail_client: GmailClient | None = None
    senders: Senders | None = None


Handler = Callable[[JobContext], Awaitable[dict[str, Any]]]
InboundRunner = Callable[
    ["JobContext", uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, NormalizedInbound], Awaitable[None]
]
ExhaustedHook = Callable[[JobContext], Awaitable[None]]

_runner: InboundRunner | None = None


def set_inbound_runner(runner: InboundRunner | None) -> None:
    global _runner
    _runner = runner


def inbound_runner_installed() -> bool:
    return _runner is not None


async def _set_event(
    session: AsyncSession, event_id: uuid.UUID, status: str, reason: str | None = None
) -> None:
    await session.execute(
        text("UPDATE inbound_events SET status = :s, skip_reason = :r WHERE id = :i"),
        {"s": status, "r": reason, "i": event_id},
    )


async def _flooded(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    limit = get_settings().customer_flood_per_minute
    since = timeutil.utc_now() - timedelta(minutes=1)
    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM messages WHERE conversation_id = :c AND direction = 'in'"
                " AND created_at >= :since"
            ),
            {"c": conversation_id, "since": since},
        )
    ).scalar_one()
    return int(count) >= limit


def _content(inbound: NormalizedInbound) -> str:
    if inbound.media_type and not inbound.text:
        return f"[{inbound.media_type}]"
    return inbound.text


async def _own_address(session: AsyncSession) -> str | None:
    row = (
        await session.execute(text("SELECT config FROM integrations WHERE kind = 'gmail'"))
    ).first()
    return str((row.config or {}).get("address", "")) or None if row else None


async def _published_policy(session: AsyncSession) -> PolicyV2 | None:
    parsed, _ = await policy_service.get_published_policy(session)
    return parsed if isinstance(parsed, PolicyV2) else None


async def _classify_email(
    ctx: JobContext,
    session: AsyncSession,
    event_id: uuid.UUID,
    inbound: NormalizedInbound,
) -> EmailClassification:
    """Email_Filter_v2.2.md Section 14 integration point: runs after prechecks
    (bounce/auto-reply/no-reply already short-circuited for free) and before any
    customer/conversation/workflow is created."""
    policy = await _published_policy(session)
    llm = await email_classify.build_llm_client(ctx.factory, ctx.job.tenant_id, ctx.transport)
    result = await email_classify.classify_email(
        llm, tenant_id=ctx.job.tenant_id, run_id=None, policy=policy, inbound=inbound
    )
    await session.execute(
        text(
            "UPDATE inbound_events SET raw_payload = raw_payload ||"
            " jsonb_build_object('email_classification', CAST(:c AS jsonb)) WHERE id = :i"
        ),
        {"c": result.model_dump_json(), "i": event_id},
    )
    return result


Staged = tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, NormalizedInbound]


async def process_inbound(ctx: JobContext) -> dict[str, Any]:
    outcome = await _ingest(ctx)
    if isinstance(outcome, dict):
        return outcome
    if _runner is None:
        return {"stage": "ingested"}
    await _runner(ctx, *outcome)
    return {"stage": "runner_done"}


async def _ingest(ctx: JobContext) -> dict[str, Any] | Staged:
    event_id = uuid.UUID(str(ctx.job.payload["inbound_event_id"]))
    tenant_id = ctx.job.tenant_id
    async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
        event = (
            await session.execute(
                text("SELECT status, raw_payload FROM inbound_events WHERE id = :i FOR UPDATE"),
                {"i": event_id},
            )
        ).first()
        if event is None:
            return {"stage": "missing_event"}
        if event.status in ("processed", "skipped"):
            return {"stage": "already_final"}
        inbound = NormalizedInbound.model_validate(event.raw_payload["normalized"])
        tenant = (
            await session.execute(
                text("SELECT status FROM tenants WHERE id = :t"), {"t": tenant_id}
            )
        ).one()
        if tenant.status != "active":
            await _set_event(session, event_id, "skipped", "TENANT_INACTIVE")
            return {"stage": "skipped", "reason": "TENANT_INACTIVE"}
        if inbound.channel == "email":
            reason = prechecks.email_skip_reason(inbound, await _own_address(session))
            if reason:
                await _set_event(session, event_id, "skipped", reason)
                return {"stage": "skipped", "reason": reason}
            classification = await _classify_email(ctx, session, event_id, inbound)
            if classification.action in ("SKIP", "SKIP_DUPLICATE", "QUARANTINE"):
                email_reason = f"EMAIL_FILTER_{classification.action}:{classification.category}"
                await _set_event(session, event_id, "skipped", email_reason)
                return {"stage": "skipped", "reason": email_reason}
        customer_id = await identity.get_or_create_customer(session, inbound)
        ref = await identity.get_or_create_conversation(session, customer_id, inbound.channel)
        await privacy.ensure_consent(session, customer_id, inbound.channel, "first_inbound")
        if await _flooded(session, ref.conversation_id):
            await _set_event(session, event_id, "skipped", "CUSTOMER_FLOOD")
            return {"stage": "skipped", "reason": "CUSTOMER_FLOOD"}
        existing = (
            await session.execute(
                text("SELECT id FROM messages WHERE inbound_event_id = :e"), {"e": event_id}
            )
        ).first()
        if existing is not None:
            message_id = existing.id
        else:
            message_id = (
                await session.execute(
                    text(
                        "INSERT INTO messages (conversation_id, direction, sender, channel, content,"
                        " external_id, inbound_event_id) VALUES (:c, 'in', 'customer',"
                        " :ch, :content, :ext, :e) RETURNING id"
                    ),
                    {
                        "c": ref.conversation_id,
                        "ch": inbound.channel,
                        "content": _content(inbound),
                        "ext": inbound.external_id,
                        "e": event_id,
                    },
                )
            ).scalar_one()
            await session.execute(
                text(
                    "UPDATE conversations SET last_inbound_at = :at, last_message_at = :at"
                    " WHERE id = :c"
                ),
                {"at": min(inbound.received_at, timeutil.utc_now()), "c": ref.conversation_id},
            )
        if keywords.is_opt_out(inbound.text):
            await session.execute(
                text(
                    "INSERT INTO opt_outs (customer_id, channel, source) VALUES (:c, :ch, 'customer_stop')"
                    " ON CONFLICT (tenant_id, customer_id, channel) DO NOTHING"
                ),
                {"c": customer_id, "ch": inbound.channel},
            )
            purpose = "whatsapp_optin" if inbound.channel == "whatsapp" else "email_contact"
            await privacy.record_consent(session, customer_id, purpose, False, "customer_stop")
            await _set_event(session, event_id, "processed", "OPT_OUT")
            return {"stage": "opt_out"}
        if keywords.is_opt_in(inbound.text):
            await session.execute(
                text("DELETE FROM opt_outs WHERE customer_id = :c AND channel = :ch"),
                {"c": customer_id, "ch": inbound.channel},
            )
            purpose = "whatsapp_optin" if inbound.channel == "whatsapp" else "email_contact"
            await privacy.record_consent(session, customer_id, purpose, True, "customer_start")
            await _set_event(session, event_id, "processed", "OPT_IN")
            return {"stage": "opt_in"}
        return tenant_id, event_id, ref.conversation_id, message_id, inbound


async def process_inbound_exhausted(ctx: JobContext) -> None:
    event_id = uuid.UUID(str(ctx.job.payload["inbound_event_id"]))
    async with session_scope(ctx.factory, tenant_id=ctx.job.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE inbound_events SET status = 'needs_review', skip_reason = 'PROCESSING_FAILED'"
                " WHERE id = :i AND status NOT IN ('processed','skipped')"
            ),
            {"i": event_id},
        )


def _gmail_client(ctx: JobContext, refresh_token: str) -> GmailClient:
    if ctx.gmail_client is not None:
        return ctx.gmail_client
    http = ExternalHttp(
        "gmail", recorder=DbCallRecorder(ctx.factory, ctx.job.tenant_id), transport=ctx.transport
    )
    return HttpGmailClient(http, refresh_token)


GMAIL_QUERY = "in:inbox newer_than:2d -from:me"
GMAIL_BATCH = 25


async def fetch_gmail(ctx: JobContext) -> dict[str, Any]:
    tenant_id = ctx.job.tenant_id
    async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
        loaded = await integrations.load_secret(session, "gmail")
        status = (
            await session.execute(text("SELECT status FROM integrations WHERE kind = 'gmail'"))
        ).scalar_one_or_none()
    if loaded is None or status != "ok":
        return {"fetched": 0, "skipped": "not_configured"}
    config, secret = loaded
    client = _gmail_client(ctx, secret["refresh_token"])
    own = config.get("address", "").lower()
    try:
        ids = await client.list_message_ids(GMAIL_QUERY, GMAIL_BATCH)
    except GmailAuthError:
        await integrations.record_health(ctx.factory, tenant_id, "gmail", False, True)
        return {"fetched": 0, "error": "auth"}
    except GmailUpstreamError:
        raise
    stored = 0
    for message_id in ids:
        async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
            known = (
                await session.execute(
                    text(
                        "SELECT 1 FROM inbound_events WHERE channel = 'email' AND external_id = :e"
                    ),
                    {"e": message_id},
                )
            ).first()
        if known is not None:
            continue
        raw = await client.get_message(message_id)
        inbound = normalize.from_gmail(raw)
        if inbound is None or (inbound.from_email or "").lower() == own:
            async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
                await session.execute(
                    text(
                        "INSERT INTO inbound_events (channel, external_id, raw_payload, status,"
                        " skip_reason) VALUES ('email', :e, '{}', 'skipped', :r)"
                        " ON CONFLICT (tenant_id, channel, external_id) DO NOTHING"
                    ),
                    {"e": message_id, "r": "OWN_ADDRESS" if inbound else "UNPARSEABLE"},
                )
            continue
        async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO inbound_events (channel, external_id, raw_payload, status)"
                        " VALUES ('email', :e, CAST(:raw AS jsonb), 'queued')"
                        " ON CONFLICT (tenant_id, channel, external_id) DO NOTHING RETURNING id"
                    ),
                    {
                        "e": message_id,
                        "raw": json.dumps({"normalized": inbound.model_dump(mode="json")}),
                    },
                )
            ).first()
            if row is not None:
                await queue.enqueue(
                    session,
                    tenant_id,
                    "process_inbound",
                    {"inbound_event_id": str(row.id)},
                    idempotency_key=f"inb:{row.id}",
                )
                stored += 1
    return {"fetched": stored}


async def integration_health(ctx: JobContext) -> dict[str, Any]:
    tenant_id = ctx.job.tenant_id
    checked: dict[str, bool] = {}
    kinds: tuple[IntegrationKind, ...] = ("shopify", "gmail", *integrations.COURIER_KINDS)
    for kind in kinds:
        async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
            exists = (
                await session.execute(
                    text("SELECT 1 FROM integrations WHERE kind = :k AND status <> 'disabled'"),
                    {"k": kind},
                )
            ).first()
        if exists is None:
            continue
        result = await integrations.probe_integration(ctx.factory, tenant_id, kind, ctx.transport)
        checked[kind] = result.ok
    return {"checked": checked}


HANDLERS: dict[str, Handler] = {
    "process_inbound": process_inbound,
    "fetch_gmail": fetch_gmail,
    "integration_health": integration_health,
}
EXHAUSTED: dict[str, ExhaustedHook] = {"process_inbound": process_inbound_exhausted}
