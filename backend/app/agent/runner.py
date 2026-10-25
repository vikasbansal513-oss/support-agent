import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine
from app.actions.senders import Senders, build_senders
from app.agent.graph import CLASSIFY_VERSION, GRAPH_VERSION, GraphDeps, build_graph
from app.agent.state import State
from app.agent.tools import ToolBox
from app.contracts.contracts import DecisionOut, NormalizedInbound
from app.core import timeutil
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import new_error_id
from app.core.flags import is_globally_paused
from app.core.mail import Mailer, get_mailer
from app.handoffs import service as handoffs
from app.integrations import service as integrations
from app.integrations.http import DbCallRecorder, ExternalHttp
from app.jobs import handlers
from app.llm.client import DbCallRecorder as LlmRecorder
from app.llm.client import LlmClient
from app.llm.provider import make_provider
from app.llm.runtime import load_effective
from app.v1.policy import AnyPolicy, PolicyV2, parse_any
from app.v1.toolbox import LiveV1ToolBox

logger = logging.getLogger("app.agent.runner")
Factory = async_sessionmaker[AsyncSession]
HISTORY_LIMIT = 6


class RetryLater(Exception):
    pass


@dataclass
class Overrides:
    llm: LlmClient | None = None
    mailer: Mailer | None = None
    tools: ToolBox | None = None
    senders: Senders | None = None


overrides = Overrides()


def _ref_forms(ref: str) -> list[str]:
    digits = ref.lstrip("#")
    return [digits, f"#{digits}"]


async def _tool_box(factory: Factory, tenant_id: uuid.UUID, transport: Any) -> ToolBox:
    if overrides.tools is not None:
        return overrides.tools
    shopify = None
    couriers: dict[str, Any] = {}
    async with session_scope(factory, tenant_id=tenant_id) as session:
        loaded = {
            kind: await integrations.load_secret(session, kind)
            for kind in ("shopify", "shiprocket", "delhivery")
        }
    for kind, item in loaded.items():
        if item is None:
            continue
        config, secret = item
        http = ExternalHttp(kind, recorder=DbCallRecorder(factory, tenant_id), transport=transport)
        if kind == "shopify":
            from app.integrations.shopify import ShopifyClient

            shopify = ShopifyClient(http, config["shop_domain"], secret["access_token"])
        else:
            couriers[kind] = integrations.build_courier(kind, tenant_id, config, secret, http)  # type: ignore[arg-type]
    return LiveV1ToolBox(tenant_id=tenant_id, shopify=shopify, couriers=couriers)


async def _llm(
    factory: Factory, tenant_id: uuid.UUID, transport: httpx.AsyncBaseTransport | None
) -> LlmClient:
    if overrides.llm is not None:
        return overrides.llm
    chosen = await load_effective(factory)
    http = ExternalHttp(
        chosen.provider,
        recorder=DbCallRecorder(factory, tenant_id),
        transport=transport,
    )
    provider = make_provider(http, chosen.provider, chosen.model, chosen.api_key, chosen.base_url)
    return LlmClient(provider, LlmRecorder(factory))


def _label(policy: AnyPolicy | None, tz: str) -> tuple[bool, str | None]:
    if policy is None:
        return True, None
    hours = policy.office_hours
    now = timeutil.utc_now()
    inside = timeutil.is_within_hours(now, hours.start, hours.end, hours.days, tz)
    if inside:
        return True, None
    opens = timeutil.next_open_utc(now, hours.start, hours.end, hours.days, tz)
    if opens is None:
        return False, None
    local = opens.astimezone(
        timeutil.IST if tz == "Asia/Kolkata" else timeutil._local(opens, tz).tzinfo
    )
    return False, local.strftime("%d %b, %I:%M %p")


async def _load_state(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    inbound: NormalizedInbound,
    run_id: uuid.UUID,
) -> tuple[State, str, int | None]:
    tenant = (
        await session.execute(
            text("SELECT mode, agent_paused FROM tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).one()
    policy_row = (
        await session.execute(
            text("SELECT version, content FROM policies WHERE status = 'published'")
        )
    ).first()
    conv = (
        await session.execute(
            text(
                "SELECT c.customer_id, c.channel, c.human_active, c.active_order_ref,"
                " c.identity_attempts, c.language, cu.phone_e164, cu.email_lower"
                " FROM conversations c JOIN customers cu ON cu.id = c.customer_id WHERE c.id = :c"
            ),
            {"c": conversation_id},
        )
    ).one()
    settings_row = (await session.execute(text("SELECT timezone FROM tenant_settings"))).first()
    tz = settings_row.timezone if settings_row else "Asia/Kolkata"
    opted = (
        await session.execute(
            text("SELECT 1 FROM opt_outs WHERE customer_id = :c AND channel = :ch"),
            {"c": conv.customer_id, "ch": conv.channel},
        )
    ).first()
    history_rows = (
        await session.execute(
            text(
                "SELECT sender, content FROM messages WHERE conversation_id = :c AND id <> :m"
                " AND direction IN ('in','out') AND (action_id IS NULL OR action_id IN"
                " (SELECT id FROM agent_actions WHERE status = 'sent'))"
                " ORDER BY created_at DESC LIMIT :n"
            ),
            {"c": conversation_id, "m": message_id, "n": HISTORY_LIMIT},
        )
    ).all()
    contacted = (
        await session.execute(
            text(
                "SELECT 1 FROM messages WHERE conversation_id = :c AND direction = 'out' AND"
                " (action_id IS NULL OR action_id IN (SELECT id FROM agent_actions WHERE status = 'sent'))"
                " LIMIT 1"
            ),
            {"c": conversation_id},
        )
    ).first()
    refs_rows = (
        await session.execute(
            text(
                "SELECT decision->>'order_ref' AS ref FROM agent_runs WHERE conversation_id = :c"
                " AND id <> :r AND status = 'done' AND decision->>'action' = 'reply'"
                " AND decision->>'order_ref' IS NOT NULL AND started_at > now() - interval '24 hours'"
            ),
            {"c": conversation_id, "r": run_id},
        )
    ).all()
    policy = parse_any(policy_row.content) if policy_row else None
    inside, opens = _label(policy, tz)
    phone, email = conv.phone_e164, conv.email_lower
    masked = _mask(phone, email)
    prior: list[str] = []
    for r in refs_rows:
        prior.extend(_ref_forms(r.ref))
    state: State = {
        "tenant_id": str(tenant_id),
        "run_id": str(run_id),
        "inbound_event_id": str(event_id),
        "conversation_id": str(conversation_id),
        "customer_id": str(conv.customer_id),
        "channel": conv.channel,
        "incoming_text": inbound.text,
        "media_type": inbound.media_type,
        "history": [{"sender": h.sender, "content": h.content} for h in reversed(history_rows)],
        "policy": policy.model_dump(mode="json") if policy else None,
        "tenant_mode": tenant.mode,
        "agent_paused": bool(tenant.agent_paused) or await is_globally_paused(session),
        "human_active": bool(conv.human_active),
        "opted_out": opted is not None,
        "first_message": contacted is None,
        "within_office_hours": inside,
        "opens_label": opens,
        "flags": [],
        "identity": {"attempts": int(conv.identity_attempts), "verified": False, "order_ref": None},
        "tool_results": {},
        "tool_errors": [],
        "trace": [],
        "customer_masked": masked,
        "customer_phone": phone,
        "customer_email": email,
        "active_order_ref": conv.active_order_ref,
        "prior_customer_refs": prior,
        "last_language": conv.language,
    }
    return state, tenant.mode, policy_row.version if policy_row else None


def _mask(phone: str | None, email: str | None) -> str | None:
    if phone:
        return f"{phone[:3]}{'*' * max(len(phone) - 6, 0)}{phone[-3:]}"
    if email and "@" in email:
        user, domain = email.split("@", 1)
        return f"{user[:1]}***@{domain}"
    return None


async def run_inbound(
    ctx: handlers.JobContext,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    inbound: NormalizedInbound,
) -> None:
    factory = ctx.factory
    settings = get_settings()
    async with session_scope(factory, tenant_id=tenant_id) as session:
        mode_row = (
            await session.execute(text("SELECT mode FROM tenants WHERE id = :t"), {"t": tenant_id})
        ).one()
        run = (
            await session.execute(
                text(
                    "INSERT INTO agent_runs (inbound_event_id, conversation_id, graph_version, mode)"
                    " VALUES (:e, :c, :g, :m) ON CONFLICT (tenant_id, inbound_event_id) DO UPDATE"
                    " SET status = 'running', started_at = now(), error_id = NULL, mode = EXCLUDED.mode"
                    " WHERE agent_runs.status IN ('running','failed') RETURNING id"
                ),
                {"e": event_id, "c": conversation_id, "g": GRAPH_VERSION, "m": mode_row.mode},
            )
        ).first()
        if run is None:
            await session.execute(
                text(
                    "UPDATE inbound_events SET status = 'processed' WHERE id = :i AND status <> 'skipped'"
                ),
                {"i": event_id},
            )
            return
        run_id: uuid.UUID = run.id
        state, mode, policy_version = await _load_state(
            session, tenant_id, event_id, conversation_id, message_id, inbound, run_id
        )
    state["attempt"] = ctx.job.attempts
    state["max_attempts"] = ctx.job.max_attempts
    toolbox = await _tool_box(factory, tenant_id, ctx.transport)
    raw_policy = state.get("policy")
    parsed = parse_any(raw_policy) if raw_policy else None
    if isinstance(parsed, PolicyV2):
        from app.agent import runner_v1

        async with session_scope(factory, tenant_id=tenant_id) as session:
            await runner_v1.load_v1_state(session, state, tenant_id, conversation_id)
        await runner_v1.run(
            ctx,
            tenant_id=tenant_id,
            event_id=event_id,
            conversation_id=conversation_id,
            run_id=run_id,
            state=state,
            policy=parsed,
            policy_version=policy_version,
            mode=mode,
            llm=await _llm(factory, tenant_id, ctx.transport),
            toolbox=toolbox,
            senders=ctx.senders
            or overrides.senders
            or await build_senders(factory, tenant_id, ctx.transport),
            mailer=overrides.mailer or get_mailer(),
            fail=_fail_run,
        )
        return
    graph = build_graph(
        GraphDeps(
            llm=await _llm(factory, tenant_id, ctx.transport),
            tools=toolbox,
            classify_min_confidence=settings.classify_min_confidence,
        )
    )
    try:
        final: State = await graph.ainvoke(
            state, config={"recursion_limit": settings.agent_recursion_limit}
        )
    except Exception as exc:
        await _fail_run(factory, tenant_id, run_id, exc)
        raise
    decision = DecisionOut.model_validate(final["decision"])
    if decision.reason_code in ("LLM_UNAVAILABLE",) and ctx.job.attempts < ctx.job.max_attempts:
        await _fail_run(factory, tenant_id, run_id, RetryLater("llm unavailable"))
        raise RetryLater("llm unavailable")
    await _persist(
        factory,
        tenant_id,
        run_id,
        event_id,
        conversation_id,
        final,
        decision,
        policy_version,
        mode,
        list(toolbox.calls),
    )
    senders = (
        ctx.senders or overrides.senders or await build_senders(factory, tenant_id, ctx.transport)
    )
    mailer = overrides.mailer or get_mailer()
    if decision.action == "skip":
        return
    if decision.reply is not None:
        await engine.submit_reply(
            factory,
            senders,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            body=decision.reply.text,
            key=f"{run_id}:reply_customer:1",
            mode=mode,
        )
    if decision.action == "handoff":
        await handoffs.create_handoff(
            factory,
            mailer,
            senders,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            decision=decision,
            mode=mode,
        )
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text("UPDATE inbound_events SET status = 'processed' WHERE id = :i"), {"i": event_id}
        )


async def _fail_run(
    factory: Factory, tenant_id: uuid.UUID, run_id: uuid.UUID, exc: Exception
) -> None:
    error_id = new_error_id()
    logger.error(
        "agent run failed", extra={"error_id": error_id, "error_class": type(exc).__name__}
    )
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET status = 'failed', error_id = :e, finished_at = now() WHERE id = :i"
            ),
            {"e": error_id, "i": run_id},
        )


async def _persist(
    factory: Factory,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    event_id: uuid.UUID,
    conversation_id: uuid.UUID,
    final: State,
    decision: DecisionOut,
    policy_version: int | None,
    mode: str,
    tool_calls: list[dict[str, Any]],
) -> None:
    stored = decision.model_dump(mode="json")
    identity = final.get("identity", {})
    classification = final.get("classification") or {}
    stored["order_ref"] = identity.get("order_ref") if identity.get("verified") else None
    stored["intent"] = classification.get("intent")
    skipped = decision.action == "skip"
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET decision = CAST(:d AS jsonb), trace = CAST(:t AS jsonb),"
                " status = :s, policy_version = :p, mode = :m,"
                " prompt_versions = CAST(:pv AS jsonb), finished_at = now() WHERE id = :i"
            ),
            {
                "d": _json(stored),
                "t": _json(final.get("trace", [])),
                "s": "skipped" if skipped else "done",
                "p": policy_version,
                "m": mode,
                "pv": _json({"classify": CLASSIFY_VERSION}) if classification else "{}",
                "i": run_id,
            },
        )
        await engine.record_tool_calls(session, run_id, conversation_id, tool_calls)
        language = classification.get("language")
        verified = bool(identity.get("verified"))
        await session.execute(
            text(
                "UPDATE conversations SET identity_attempts = :a,"
                " active_order_ref = CASE WHEN :v THEN :ref ELSE active_order_ref END,"
                " language = COALESCE(CAST(:lang AS text), language) WHERE id = :c"
            ),
            {
                "a": int(identity.get("attempts", 0)),
                "v": verified,
                "ref": identity.get("order_ref"),
                "lang": language,
                "c": conversation_id,
            },
        )
        if skipped:
            await session.execute(
                text(
                    "UPDATE inbound_events SET status = 'skipped', skip_reason = :r WHERE id = :i"
                ),
                {"r": decision.reason_code, "i": event_id},
            )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def install() -> None:
    handlers.set_inbound_runner(run_inbound)
