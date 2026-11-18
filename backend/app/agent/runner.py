import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine
from app.actions.senders import Senders, build_senders
from app.agent import templates as agent_templates
from app.agent.graph import CLASSIFY_VERSION, GRAPH_VERSION, GraphDeps, build_graph
from app.agent.state import State
from app.agent.tools import ToolBox
from app.channels import identity
from app.contracts.contracts import DecisionOut, NormalizedInbound
from app.core import officehours, timeutil
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
from app.policy import service as policy_service
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
    shopify: Any = None
    couriers: dict[str, Any] = {}
    # ROOT-CAUSE FIX (2026-11-17): was a sequential per-kind
    # `{kind: await integrations.load_secret(session, kind) for kind in (...)}` loop -
    # 9 DB round-trips (shopify, woocommerce + 7 courier kinds) measured as a real
    # contributor to a ~1.6s pre-LLM delay on a live voice call. load_secrets() fetches
    # all of them in a single round-trip. See app.integrations.service.load_secrets.
    async with session_scope(factory, tenant_id=tenant_id) as session:
        loaded = await integrations.load_secrets(
            session, ("shopify", "woocommerce", *integrations.COURIER_KINDS)
        )
    for kind, item in loaded.items():
        if item is None:
            continue
        config, secret = item
        http = ExternalHttp(kind, recorder=DbCallRecorder(factory, tenant_id), transport=transport)
        if kind == "shopify":
            from app.integrations.shopify import ShopifyClient

            shopify = ShopifyClient(http, config["shop_domain"], secret["access_token"])
        elif kind == "woocommerce":
            from app.integrations.woocommerce import WooCommerceClient

            # One store platform per brand; Shopify wins if both rows somehow exist.
            if shopify is None:
                shopify = WooCommerceClient(
                    http,
                    config["store_url"],
                    secret["consumer_key"],
                    secret["consumer_secret"],
                )
        else:
            couriers[kind] = integrations.build_courier(kind, tenant_id, config, secret, http)
    return LiveV1ToolBox(tenant_id=tenant_id, shopify=shopify, couriers=couriers)


# ROOT-CAUSE FIX (2026-11-17): default ExternalHttp retry/backoff (timeout=10s,
# max_attempts=3, backoff up to 30s - see app.integrations.http) can take up to
# ~100s in the worst case (confirmed from a real voice call's logs: a 99.7s silent
# gap between STT finishing and the agent's reply). That's invisible on WhatsApp/
# email, where a slow reply just arrives late, but unacceptable on a live phone
# call - a caller will not wait 100s of silence. These are only used when
# NormalizedInbound.is_live_call is True (set exclusively by app.voice.service),
# so WhatsApp/email keep the original defaults unchanged.
LIVE_CALL_LLM_TIMEOUT_S = 6.0
LIVE_CALL_LLM_MAX_ATTEMPTS = 1


async def _llm(
    factory: Factory,
    tenant_id: uuid.UUID,
    transport: httpx.AsyncBaseTransport | None,
    *,
    fast: bool = False,
) -> LlmClient:
    if overrides.llm is not None:
        return overrides.llm
    chosen = await load_effective(factory)
    http_kwargs: dict[str, Any] = {
        "recorder": DbCallRecorder(factory, tenant_id),
        "transport": transport,
    }
    if fast:
        http_kwargs["timeout"] = LIVE_CALL_LLM_TIMEOUT_S
        http_kwargs["max_attempts"] = LIVE_CALL_LLM_MAX_ATTEMPTS
    http = ExternalHttp(chosen.provider, **http_kwargs)
    provider = make_provider(http, chosen.provider, chosen.model, chosen.api_key, chosen.base_url)
    return LlmClient(provider, LlmRecorder(factory))


def _label(policy: AnyPolicy | None, tz: str) -> tuple[bool, str | None]:
    if policy is None:
        return True, None
    now = timeutil.utc_now()
    inside = officehours.is_open(policy, now, tz)
    if inside:
        return True, None
    opens = officehours.next_open(policy, now, tz)
    if opens is None:
        return False, None
    local = opens.astimezone(
        timeutil.IST if tz == "Asia/Kolkata" else timeutil._local(opens, tz).tzinfo
    )
    return False, local.strftime("%d %b, %I:%M %p")


async def _fetch_tenant_row(factory: Factory, tenant_id: uuid.UUID) -> Any:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return (
            await session.execute(
                text("SELECT mode, agent_paused, name FROM tenants WHERE id = :t"), {"t": tenant_id}
            )
        ).one()


async def _fetch_policy_row(
    factory: Factory, tenant_id: uuid.UUID
) -> tuple[Any | None, int | None]:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return await policy_service.get_published_policy(session)


async def _fetch_conversation_row(
    factory: Factory, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> Any:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return (
            await session.execute(
                text(
                    "SELECT c.customer_id, c.channel, c.human_active, c.active_order_ref,"
                    " c.identity_attempts, c.language, cu.phone_e164, cu.email_lower, cu.name"
                    " FROM conversations c JOIN customers cu ON cu.id = c.customer_id WHERE c.id = :c"
                ),
                {"c": conversation_id},
            )
        ).one()


async def _fetch_settings_row(factory: Factory, tenant_id: uuid.UUID) -> Any:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return (await session.execute(text("SELECT timezone FROM tenant_settings"))).first()


async def _fetch_history_rows(
    factory: Factory, tenant_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> Any:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return (
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


async def _fetch_contacted_row(
    factory: Factory, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> Any:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return (
            await session.execute(
                text(
                    "SELECT 1 FROM messages WHERE conversation_id = :c AND direction = 'out' AND"
                    " (action_id IS NULL OR action_id IN (SELECT id FROM agent_actions WHERE status = 'sent'))"
                    " LIMIT 1"
                ),
                {"c": conversation_id},
            )
        ).first()


async def _fetch_refs_rows(
    factory: Factory, tenant_id: uuid.UUID, conversation_id: uuid.UUID, run_id: uuid.UUID
) -> Any:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return (
            await session.execute(
                text(
                    "SELECT decision->>'order_ref' AS ref FROM agent_runs WHERE conversation_id = :c"
                    " AND id <> :r AND status = 'done' AND decision->>'action' = 'reply'"
                    " AND decision->>'order_ref' IS NOT NULL AND started_at > now() - interval '24 hours'"
                ),
                {"c": conversation_id, "r": run_id},
            )
        ).all()


async def _fetch_opted_row(
    factory: Factory, tenant_id: uuid.UUID, customer_id: uuid.UUID, channel: str
) -> Any:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return (
            await session.execute(
                text("SELECT 1 FROM opt_outs WHERE customer_id = :c AND channel = :ch"),
                {"c": customer_id, "ch": channel},
            )
        ).first()


async def _fetch_global_pause(factory: Factory, tenant_id: uuid.UUID) -> bool:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return await is_globally_paused(session)


async def _load_state(
    factory: Factory,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    inbound: NormalizedInbound,
    run_id: uuid.UUID,
) -> tuple[State, str, int | None]:
    # PERFORMANCE FIX (2026-11-18): this function used to run 8 DB reads one after
    # another on a single shared session - a real voice call's "run_inbound timing:
    # load_state done" log showed this step alone taking ~1.9s, which is pure dead
    # air to a live caller on a phone call (see app.voice.service.TURN_TIMEOUT_S's own
    # comment on why pre-LLM DB work has to stay small). Of the 8 reads, only `opted`
    # depends on another read's result (conv.customer_id/channel) - the other 7 are
    # fully independent of each other and of `opted`, so they now run concurrently,
    # each on its own connection (same asyncio.gather pattern already used below for
    # tool_box/v1_state/llm_client), cutting ~7 sequential round trips down to
    # roughly the slowest single one. `opted` then runs right after `conv` resolves -
    # one extra round trip, not eight.
    # This trades one shared transaction for several independent read-only ones,
    # which is safe here: every query is a plain read, nothing here needs to see the
    # agent_runs row this same turn just inserted, and none of the 7 need each
    # other's result.
    # VERIFY: not run against a live DB from here (no pytest/app execution per this
    # change's own constraints) - only checked with `python -m py_compile` for syntax.
    # Confirm on the next real call that "run_inbound timing: load_state done" drops
    # from ~1.9s toward ~0.3-0.5s, and that no tenant/RLS error appears (each helper
    # opens its own tenant-scoped session via session_scope, same as the existing
    # concurrent block below). Real end-to-end turn time also depends heavily on DB
    # *connection* latency (a separate, still-open question - see
    # app.voice.service._context's own "session acquired" timing log, which has shown
    # multi-second connection-acquisition times on some calls, most likely Neon
    # cold-start/region latency rather than anything in this app's code) - this fix
    # only removes this app's own needless serialization, it does not fix a slow
    # underlying DB connection.
    (
        tenant,
        (policy, policy_version),
        conv,
        settings_row,
        history_rows,
        contacted,
        refs_rows,
        global_paused,
    ) = await asyncio.gather(
        _fetch_tenant_row(factory, tenant_id),
        _fetch_policy_row(factory, tenant_id),
        _fetch_conversation_row(factory, tenant_id, conversation_id),
        _fetch_settings_row(factory, tenant_id),
        _fetch_history_rows(factory, tenant_id, conversation_id, message_id),
        _fetch_contacted_row(factory, tenant_id, conversation_id),
        _fetch_refs_rows(factory, tenant_id, conversation_id, run_id),
        _fetch_global_pause(factory, tenant_id),
    )
    opted = await _fetch_opted_row(factory, tenant_id, conv.customer_id, conv.channel)
    tz = settings_row.timezone if settings_row else "Asia/Kolkata"
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
        "tenant_name": tenant.name,
        "incoming_text": inbound.text,
        "media_type": inbound.media_type,
        "history": [{"sender": h.sender, "content": h.content} for h in reversed(history_rows)],
        "policy": policy.model_dump(mode="json") if policy else None,
        "tenant_mode": tenant.mode,
        "agent_paused": bool(tenant.agent_paused) or global_paused,
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
        "customer_name": conv.name,
        "active_order_ref": conv.active_order_ref,
        "prior_customer_refs": prior,
        "last_language": conv.language,
    }
    return state, tenant.mode, policy_version


def _mask(phone: str | None, email: str | None) -> str | None:
    if phone:
        return f"{phone[:3]}{'*' * max(len(phone) - 6, 0)}{phone[-3:]}"
    if email and "@" in email:
        user, domain = email.split("@", 1)
        return f"{user[:1]}***@{domain}"
    return None


async def _link_identities_from_order(factory: Factory, tenant_id: uuid.UUID, final: State) -> None:
    """V0 counterpart of app.agent.runner_v1._link_identities_from_order - same best-effort
    omnichannel identity link, for tenants still on the V0 (read-only) graph."""
    order_raw = (final.get("tool_results") or {}).get("order")
    if not order_raw:
        return
    phone, email = order_raw.get("customer_phone"), order_raw.get("customer_email")
    name = order_raw.get("customer_name")
    if not phone and not email:
        return
    try:
        async with session_scope(factory, tenant_id=tenant_id) as session:
            await identity.link_customer_identities(session, phone, email)
            await identity.link_customer_name(session, phone, email, name)
    except Exception:
        logger.exception("customer identity link failed")


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
    # TEMPORARY DIAGNOSTIC (2026-11-17): a real voice call's SECOND turn (first turn that
    # reaches this function - the first turn is often consumed by voice consent-capture)
    # has shown a reproducible ~45-60s gap between STT finishing and this turn's reply LLM
    # call completing, on two separate real calls, with no existing log line inside that
    # gap to say which stage it's in. These timings bracket every stage of this function
    # (DB state load, tool/secrets setup, LLM client setup, actual graph/agent execution)
    # so the next call's logs show exactly which one is slow. Remove once identified.
    _t0 = time.monotonic()
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
    # PERFORMANCE FIX (2026-11-18): _load_state now opens its own tenant-scoped
    # sessions concurrently (see its own docstring-comment) instead of sharing this
    # block's session, so it's called after this `async with` block closes rather
    # than inside it - it no longer needs (or accepts) this session.
    state, mode, policy_version = await _load_state(
        factory, tenant_id, event_id, conversation_id, message_id, inbound, run_id
    )
    logger.info(
        "run_inbound timing: load_state done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    state["attempt"] = ctx.job.attempts
    state["max_attempts"] = ctx.job.max_attempts
    raw_policy = state.get("policy")
    parsed = parse_any(raw_policy) if raw_policy else None
    if isinstance(parsed, PolicyV2):
        from app.agent import runner_v1

        async def _load_v1_state_task() -> None:
            async with session_scope(factory, tenant_id=tenant_id) as session:
                await runner_v1.load_v1_state(session, state, tenant_id, conversation_id, parsed)

        # PERFORMANCE FIX (2026-11-18): tool_box, v1 state load, and LLM-client creation
        # each only need factory/tenant_id/transport (or an independent DB session) - none
        # of them depends on either of the other two's result - but were previously awaited
        # one after another, so their times added up. A real voice call's logs
        # (cpu_boost_test.txt, 2026-11-18) showed tool_box=638ms + v1_state=2043ms +
        # llm_client=654ms = ~3.3s spent in sequence here, inside an 11.4s total turn that
        # ran long enough for the caller to hang up before the reply's TTS audio could be
        # sent. Running these three concurrently with asyncio.gather cuts that ~3.3s to
        # roughly the slowest of the three (~2s), saving ~1.3s of pure silence on every
        # live call, at zero infra cost. This only touches the V1 (PolicyV2) path, which is
        # the path real voice calls use - the V0 branch below is untouched.
        toolbox, _, llm_client = await asyncio.gather(
            _tool_box(factory, tenant_id, ctx.transport),
            _load_v1_state_task(),
            _llm(factory, tenant_id, ctx.transport, fast=inbound.is_live_call),
        )
        logger.info(
            "run_inbound timing: parallel tool_box/v1_state/llm_client done elapsed_ms=%d",
            int((time.monotonic() - _t0) * 1000),
        )
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
            llm=llm_client,
            toolbox=toolbox,
            senders=ctx.senders
            or overrides.senders
            or await build_senders(factory, tenant_id, ctx.transport),
            mailer=overrides.mailer or get_mailer(),
            fail=_fail_run,
        )
        logger.info(
            "run_inbound timing: v1 run done elapsed_ms=%d",
            int((time.monotonic() - _t0) * 1000),
        )
        return
    toolbox = await _tool_box(factory, tenant_id, ctx.transport)
    logger.info(
        "run_inbound timing: tool_box done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    llm_client = await _llm(factory, tenant_id, ctx.transport, fast=inbound.is_live_call)
    logger.info(
        "run_inbound timing: llm client ready elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    graph = build_graph(
        GraphDeps(
            llm=llm_client,
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
    logger.info(
        "run_inbound timing: graph.ainvoke done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
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
    await _link_identities_from_order(factory, tenant_id, final)
    senders = (
        ctx.senders or overrides.senders or await build_senders(factory, tenant_id, ctx.transport)
    )
    mailer = overrides.mailer or get_mailer()
    if decision.action == "skip":
        return
    handoff_id: uuid.UUID | None = None
    if decision.action == "handoff":
        handoff_id = await handoffs.create_handoff(
            factory,
            mailer,
            senders,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            decision=decision,
            mode=mode,
        )
    # Decision Framework Sections 3/4/8/9: every complaint/investigation/escalation handed
    # to a human gets the customer a reference number. create_handoff() must run first to
    # mint the id, so the reference line is folded into the SAME outbound reply as ONE
    # engine.submit_reply call, not a second one - draft/shadow review and several existing
    # tests count actions 1:1 against each handoff, and a second action per handoff broke
    # that count. When there's no reply at all (decision.reply is None), the reference line
    # is sent on its own so the customer isn't left with nothing; if folding would overflow
    # the 1500-char text limit (very rare), it falls back to a second, separate send.
    # AGENT_PAUSED is the one established exception (SILENT_REASONS in app/agent/graph.py,
    # test_paused_agent_hands_off_silently asserts zero reply_customer actions): a paused
    # agent must stay fully silent, so no reference line is synthesized for it either.
    reply_text = decision.reply.text if decision.reply is not None else None
    extra: str | None = None
    if handoff_id is not None and decision.reason_code != "AGENT_PAUSED":
        lang = decision.handoff.language if decision.handoff else "hinglish"
        ref_line = agent_templates.render(
            "handoff_reference", lang, ref=handoffs.reference_code(handoff_id)
        )
        if reply_text is not None:
            combined = f"{reply_text}\n\n{ref_line}"
            if len(combined) <= 1500:
                reply_text = combined
            else:
                extra = ref_line
        else:
            reply_text = ref_line
    if reply_text is not None:
        await engine.submit_reply(
            factory,
            senders,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            body=reply_text,
            key=f"{run_id}:reply_customer:1",
            mode=mode,
        )
    if extra is not None:
        await engine.submit_reply(
            factory,
            senders,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            body=extra,
            key=f"{run_id}:handoff_ref:1",
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
