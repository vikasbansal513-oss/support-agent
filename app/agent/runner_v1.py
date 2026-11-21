import json
import logging
import time
import uuid
from datetime import date
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine, writes
from app.actions.senders import Senders
from app.agent.graph import _lang
from app.agent.state import State
from app.agent.tools import ToolBox
from app.channels import identity
from app.core import timeutil
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError
from app.core.mail import Mailer
from app.handoffs import service as handoffs
from app.integrations.shopify_write import get_writer_for_tenant
from app.jobs import handlers
from app.llm.client import LlmClient
from app.v1 import convstate, outcome
from app.v1 import templates as t
from app.v1.contracts import TOOL_TIER, WRITE_TOOLS, DecisionV1, V1Reply
from app.v1.gate import GateDecision
from app.v1.graph import GRAPH_VERSION_V1, PLAN_VERSION, V1Deps, build_graph_v1, handoff_decision
from app.v1.policy import PolicyV2
from app.v1.regate import customer_context, regate
from app.v1.reply import REPLY_VERSION
from app.v1.toolbox import ToolBoxFacts, V1ToolBox

logger = logging.getLogger("app.agent.runner_v1")
Factory = async_sessionmaker[AsyncSession]
ACTIVE_OR_DONE = (
    "awaiting_customer",
    "pending_approval",
    "approved",
    "claimed",
    "executed",
    "confirmed",
)


class RetryLater(Exception):
    pass


async def _v1_fetch_conv_row(
    session: AsyncSession, conversation_id: uuid.UUID
) -> convstate.ConvRow:
    return await convstate.load(session, conversation_id)


async def _v1_fetch_customer_context(
    session: AsyncSession, conversation_id: uuid.UUID, day: date, window_hours: int
) -> tuple[int, bool, int, int]:
    return await customer_context(session, conversation_id, "refund_create", day, window_hours)


async def _v1_fetch_killed(session: AsyncSession) -> dict[str, bool]:
    # ROOT-CAUSE FIX (2026-11-17): was `{tool: await writes.killed(session, tool) for
    # tool in WRITE_TOOLS}` - 5 sequential DB round-trips (one per WRITE_TOOLS entry,
    # each itself issuing 2 queries) measured as a real contributor to a ~3s pre-LLM
    # delay on a live voice call. killed_many() gets the identical per-tool result in
    # 2 round-trips total. See app.actions.writes.killed_many for details.
    return await writes.killed_many(session, WRITE_TOOLS)


async def _v1_fetch_prior_writes(session: AsyncSession) -> Any:
    return (
        await session.execute(
            text(
                "SELECT tool_name, order_ref FROM agent_actions WHERE tool_name IN"
                " ('address_update','exchange_request') AND status = ANY(:s)"
            ),
            {"s": list(ACTIVE_OR_DONE)},
        )
    ).all()


async def _v1_fetch_customer_id(session: AsyncSession, conversation_id: uuid.UUID) -> uuid.UUID:
    return await writes._customer_of(session, conversation_id)


async def _v1_fetch_pending_action(
    session: AsyncSession, action_id: uuid.UUID
) -> writes.ActionRow | None:
    return await writes.get_action(session, action_id)


async def _v1_fetch_claims(session: AsyncSession, customer_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT a.tool_name, a.order_ref, a.conversation_id, a.status FROM agent_actions a"
                " JOIN conversations c ON c.id = a.conversation_id"
                " WHERE c.customer_id = :cu AND a.tool_name IS NOT NULL AND a.status = ANY(:s)"
            ),
            {"cu": customer_id, "s": list(ACTIVE_OR_DONE)},
        )
    ).all()


async def load_v1_state(
    factory: Factory,
    state: State,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    policy: PolicyV2 | None = None,
) -> convstate.ConvRow:
    # PERFORMANCE FIX (2026-11-19, supersedes the 2026-11-19 fix below it that this
    # replaces): that earlier fix (same day, an earlier session) made these 7 reads
    # run concurrently via asyncio.gather, each opening its OWN session_scope - the
    # exact same mistake already identified and corrected once in
    # app.agent.runner._load_state and app.voice.service._context (see their
    # comments): gather() running several session_scope() checkouts concurrently
    # only overlaps each checkout's cost, it does not remove it, and on this DB/
    # network path (cross-region Cloud Run asia-south1 <-> Neon ap-southeast-1,
    # confirmed 2026-11-19 - already on Neon's pooled endpoint, so that wasn't it)
    # each checkout alone has been measured costing several hundred ms to multiple
    # seconds. 7 separate checkouts here meant paying that cost up to 7 times.
    # FIX: one shared session for this whole function (checked out once), running
    # the same 7 reads one after another on it - `action` (needs
    # row.pending_action_id) and `claims` (needs customer_id) still run after the
    # reads they depend on, exactly as before, just not on their own connections.
    # The state this function builds and its return value are unchanged.
    # VERIFY: not exercised against a live DB from here (no app execution/pytest per
    # this change's own constraints) - only checked with py_compile/ruff. Confirm on
    # the next real voice call's logs that "run_inbound timing: parallel
    # tool_box/v1_state/llm_client done" drops, matching the drop already confirmed
    # for app.voice.service._context after the same fix there.
    day: date = timeutil.to_ist(timeutil.utc_now()).date()
    window_hours = policy.fraud_cluster_window_hours if policy is not None else 24
    async with session_scope(factory, tenant_id=tenant_id) as session:
        row = await _v1_fetch_conv_row(session, conversation_id)
        refunded, recent, brand, recent_claims = await _v1_fetch_customer_context(
            session, conversation_id, day, window_hours
        )
        killed = await _v1_fetch_killed(session)
        prior = await _v1_fetch_prior_writes(session)
        customer_id = await _v1_fetch_customer_id(session, conversation_id)
        action: writes.ActionRow | None = None
        if row.pending_action_id is not None:
            action = await _v1_fetch_pending_action(session, row.pending_action_id)
        claims = await _v1_fetch_claims(session, customer_id)
    pending: dict[str, Any] | None = None
    if action is not None:
        pending = {
            "id": str(action.id),
            "tool": action.tool,
            "order_ref": action.order_ref,
            "amount_paise": action.amount_paise,
            "status": action.status,
            "expired": action.expires_at is not None and action.expires_at <= timeutil.utc_now(),
        }
    state["conv"] = {
        "awaiting": row.awaiting,
        "unclear_count": row.unclear_count,
        "turn_count": row.turn_count,
        "write_count": row.write_count,
        "order_ref": row.context.get("order_ref"),
        "pending": pending,
    }
    state["gate_ctx"] = {
        "refunded_today": refunded,
        "recent_refund": recent,
        "brand_today": brand,
        "killed": killed,
        "recent_claims": recent_claims,
    }
    state["prior_writes"] = [{"tool": r.tool_name, "order_ref": r.order_ref} for r in prior]
    # Omnichannel duplicate guard (app.v1.graph's propose()): every in-flight/done write
    # action this same customer has, on ANY of their conversations/channels - not just this
    # one. customer_id is the one app.channels.identity.link_customer_identities keeps
    # unified across WhatsApp/Email/voice, so this naturally covers "emailed a cancel, then
    # WhatsApped the same cancel" once that link exists; before it's learned (e.g. first-ever
    # contact on a second channel), the two conversations still have separate customer rows
    # and this is a no-op, same as today.
    state["active_claims"] = [
        {
            "tool": r.tool_name,
            "order_ref": r.order_ref,
            "conversation_id": str(r.conversation_id),
            "status": r.status,
        }
        for r in claims
    ]
    return row


async def _link_identities_from_order(session: AsyncSession, final: State) -> None:
    """Best-effort omnichannel identity link (see app.channels.identity): if this turn
    looked up a Shopify order, use its own phone+email to fold any separate per-channel
    `customers` rows for them into one. Never raises - this is an enrichment, not something
    that should fail an otherwise-successful agent turn.

    PERFORMANCE FIX (2026-11-21): was `async with session_scope(factory, tenant_id=tenant_id)`
    - its own separate DB checkout, on top of the ones `_persist` and `_load_row` already
    open right before/after it in `run()`, on a DB/network path where each checkout alone
    has been measured costing several hundred ms to multiple seconds (see `load_v1_state`'s
    comment) - a real contributor to a live voice call never getting a reply before the
    caller hung up. Now takes the SAME session `run()` already has open for `_persist` and
    `_load_row`, cutting 3 checkouts here down to 1. Runs in its own SAVEPOINT
    (`session.begin_nested()`), not directly in the shared transaction, so this function's
    own "never raises, never fails the turn" guarantee still holds exactly as before: a
    failure here only rolls back this savepoint, leaving `_persist`'s already-written rows
    and the shared session itself healthy for `_load_row` (and anything else in `run()`)
    right after. VERIFY: not exercised against a live DB from here (no app execution/pytest
    per this change's own constraints) - only checked with py_compile/ruff. Confirm on the
    next real voice call's logs that "persist"/"link_identities"/"load_row" timings collapse
    close together (one checkout's cost instead of three)."""
    order_raw = (final.get("tool_results") or {}).get("order")
    if not order_raw:
        return
    phone, email = order_raw.get("customer_phone"), order_raw.get("customer_email")
    name = order_raw.get("customer_name")
    if not phone and not email:
        return
    try:
        async with session.begin_nested():
            await identity.link_customer_identities(session, phone, email)
            await identity.link_customer_name(session, phone, email, name)
    except Exception:
        logger.exception("customer identity link failed")


async def run(
    ctx: handlers.JobContext,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    state: State,
    policy: PolicyV2,
    policy_version: int | None,
    mode: str,
    llm: LlmClient,
    toolbox: ToolBox,
    senders: Senders,
    mailer: Mailer,
    fail: Any,
) -> None:
    # TEMPORARY DIAGNOSTIC (2026-11-19): a real voice call's "run_inbound timing"
    # logs (app.agent.runner) showed "v1 run done" taking ~7-50+ seconds AFTER the
    # LLM call itself already returned (confirmed via the httpx logger's own
    # "POST .../chat/completions 200 OK" line, which appears within ~1s of
    # "parallel tool_box/v1_state/llm_client done") - i.e. the delay is somewhere
    # in THIS function, after graph.ainvoke(), with zero existing log output to say
    # which step. Each of _persist/_link_identities_from_order/_load_row/
    # _handoff/_confirm_yes/_confirm_no/_plain below opens its own DB session(s)
    # (same session_scope()-per-call pattern already fixed once in
    # app.agent.runner._load_state and app.voice.service._context for a confirmed
    # multi-second per-checkout cost on this DB/network path) - there are several
    # more such checkouts here that weren't touched by that fix. These checkpoints
    # narrow down which one(s). Remove once identified (then fix the same way, by
    # consolidating checkouts, rather than leaving this logging in place).
    _t0 = time.monotonic()
    factory = ctx.factory
    settings = get_settings()
    box = cast(V1ToolBox, toolbox)
    graph = build_graph_v1(
        V1Deps(llm=llm, tools=box, min_confidence=settings.classify_min_confidence)
    )
    try:
        final: State = await graph.ainvoke(
            state, config={"recursion_limit": settings.agent_recursion_limit}
        )
    except Exception as exc:
        await fail(factory, tenant_id, run_id, exc)
        raise
    logger.info(
        "v1 run timing: graph.ainvoke done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    decision = DecisionV1.model_validate(final["decision_v1"])
    if decision.reason_code == "LLM_UNAVAILABLE" and ctx.job.attempts < ctx.job.max_attempts:
        await fail(factory, tenant_id, run_id, RetryLater("llm unavailable"))
        raise RetryLater("llm unavailable")
    # PERFORMANCE FIX (2026-11-21): _persist, _link_identities_from_order and _load_row
    # each used to open their OWN session_scope() - 3 separate DB checkouts back-to-back,
    # on top of every other checkout already in this function's path (run_inbound's own
    # insert, _load_state, tool_box/v1_state/llm_client, graph.ainvoke's tool calls, the
    # action branch below, and the final inbound_events update), on a DB/network path
    # where each checkout alone has been measured costing several hundred ms to multiple
    # seconds (see load_v1_state's comment - already fixed there and in
    # app.agent.runner._load_state/app.voice.service._context the same way). This was the
    # one place left un-fixed in THIS function. One shared session for these 3 steps only
    # (not merged with the action branch below, which calls out to Shopify/email and needs
    # its own short-lived sessions so a connection is never held idle through a slow
    # external call) cuts 3 checkouts down to 1 here.
    # VERIFY: not exercised against a live DB from here (no app execution/pytest per this
    # change's own constraints) - only checked with py_compile/ruff. Confirm on the next
    # real voice call's logs that "persist"/"link_identities"/"load_row" timings collapse
    # close together instead of each costing its own multi-second gap.
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await _persist(
            session,
            run_id,
            event_id,
            conversation_id,
            final,
            decision,
            policy_version,
            mode,
            list(toolbox.calls),
        )
        logger.info(
            "v1 run timing: persist done elapsed_ms=%d",
            int((time.monotonic() - _t0) * 1000),
        )
        await _link_identities_from_order(session, final)
        logger.info(
            "v1 run timing: link_identities done elapsed_ms=%d",
            int((time.monotonic() - _t0) * 1000),
        )
        if decision.action == "skip":
            return
        row = await _load_row(session, conversation_id)
        logger.info(
            "v1 run timing: load_row done elapsed_ms=%d",
            int((time.monotonic() - _t0) * 1000),
        )
    if decision.action == "handoff":
        await _handoff(
            factory, senders, mailer, tenant_id, conversation_id, run_id, row, decision, mode
        )
    elif decision.confirm == "yes":
        await _confirm_yes(
            factory,
            senders,
            mailer,
            tenant_id,
            conversation_id,
            run_id,
            row,
            final,
            policy,
            box,
            mode,
        )
    elif decision.confirm == "no":
        await _confirm_no(factory, senders, tenant_id, conversation_id, run_id, row, final, mode)
    else:
        await _plain(
            factory,
            senders,
            tenant_id,
            conversation_id,
            run_id,
            row,
            decision,
            policy,
            mode,
            final,
            mailer,
        )
    logger.info(
        "v1 run timing: action done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text("UPDATE inbound_events SET status = 'processed' WHERE id = :i"), {"i": event_id}
        )
    logger.info(
        "v1 run timing: inbound_events updated elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )


async def _load_row(session: AsyncSession, conversation_id: uuid.UUID) -> convstate.ConvRow:
    # PERFORMANCE FIX (2026-11-21): was its own session_scope() checkout - see run()'s
    # comment where this is now called with the same shared session as _persist and
    # _link_identities_from_order. Still used standalone nowhere else (verified: no other
    # caller in app/ or tests/), so this signature change is self-contained to this file.
    return await convstate.load(session, conversation_id)


async def _save(
    factory: Factory, tenant_id: uuid.UUID, conversation_id: uuid.UUID, row: convstate.ConvRow
) -> None:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await convstate.save(session, conversation_id, row)


async def _send(
    factory: Factory,
    senders: Senders,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    reply: V1Reply,
    mode: str,
) -> None:
    # Decision Framework Section 17.2: on a voice call, a confirm_*/done_* reply reads an
    # order ref/amount as a yes-or-no ask or a just-completed-action statement - this is
    # the only point in the pipeline that still has the template_id (engine.submit_reply's
    # DB row only keeps rendered text), so the critical flag is decided here and carried
    # through submit_reply -> CaptureVoiceSender for voice.service._finish_turn to read.
    # It is a no-op for WhatsApp/email, which never read this flag.
    await engine.submit_reply(
        factory,
        senders,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        body=reply.text,
        key=f"{run_id}:reply_customer:1",
        mode=mode,
        critical=reply.template_id in t.CRITICAL_VOICE_TEMPLATES,
    )


async def _handoff(
    factory: Factory,
    senders: Senders,
    mailer: Mailer,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    row: convstate.ConvRow,
    decision: DecisionV1,
    mode: str,
) -> None:
    if row.pending_action_id is not None:
        await writes.close_pending(factory, tenant_id, row.pending_action_id, "expired", "HANDOFF")
    convstate.apply_update(row, decision.conv)
    row.awaiting = "none"
    await _save(factory, tenant_id, conversation_id, row)
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
    # mint the id, so the reference line is folded into the SAME acknowledgement reply as
    # ONE engine.submit_reply call, not a second one - draft/shadow review and several
    # existing tests count actions 1:1 against each handoff, and a second action per
    # handoff broke that count. When there's no acknowledgement reply at all (decision.reply
    # is None), the reference line is sent on its own so the customer isn't left with
    # nothing; if folding would overflow V1Reply's 1500-char limit (very rare), it falls
    # back to a second, separate send instead of raising. AGENT_PAUSED is the one
    # established exception (build_graph_v1's handoff() sets ack=None for it on purpose,
    # test_paused_agent_hands_off_silently asserts zero reply_customer actions): a paused
    # agent must stay fully silent, so no reference line is synthesized for it either.
    reply = decision.reply
    extra: str | None = None
    if handoff_id is not None and decision.reason_code != "AGENT_PAUSED":
        lang = decision.handoff.language if decision.handoff else "hinglish"
        ref_line = t.render("handoff_reference", lang, ref=handoffs.reference_code(handoff_id))
        if reply is not None:
            combined = f"{reply.text}\n\n{ref_line}"
            if len(combined) <= 1500:
                reply = reply.model_copy(update={"text": combined})
            else:
                extra = ref_line
        else:
            reply = V1Reply(template_id="handoff_reference", language=lang, text=ref_line)
    if reply is not None:
        await _send(factory, senders, tenant_id, conversation_id, run_id, reply, mode)
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
            critical=True,
        )


async def _plain(
    factory: Factory,
    senders: Senders,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    row: convstate.ConvRow,
    decision: DecisionV1,
    policy: PolicyV2,
    mode: str,
    final: State,
    mailer: Mailer,
) -> None:
    update = decision.conv
    if decision.intent is not None:
        intent = decision.intent
        if mode != "auto":
            update = update.model_copy(update={"awaiting": "none", "clear_pending": True})
            convstate.apply_update(row, update)
            await _save(factory, tenant_id, conversation_id, row)
        else:
            gate = GateDecision(
                intent.route, intent.gate_reason, None, intent.amount_paise, TOOL_TIER[intent.tool]
            )
            try:
                async with session_scope(factory, tenant_id=tenant_id) as session:
                    proposal = await writes.propose(
                        session,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        tool=intent.tool,
                        order_ref=intent.order_ref,
                        amount_paise=intent.amount_paise,
                        args=intent.args,
                        decision=gate,
                        policy=policy,
                        write_count=row.write_count,
                    )
                    convstate.apply_update(row, update)
                    if proposal.created:
                        row.pending_action_id = proposal.action.id
                        row.context["confirm_token"] = proposal.token
                        snapshot = outcome.order_snapshot(final)
                        if snapshot is not None:
                            row.context["order_snapshot"] = snapshot
                    await convstate.save(session, conversation_id, row)
            except IntegrityError:
                logger.error("write proposal conflicted", extra={"run_id": str(run_id)})
                failed = handoff_decision({**final, "flags": []}, "ACTION_FAILED")
                await _handoff(
                    factory, senders, mailer, tenant_id, conversation_id, run_id, row, failed, mode
                )
                return
    else:
        convstate.apply_update(row, update)
        await _save(factory, tenant_id, conversation_id, row)
    if decision.reply is not None:
        await _send(factory, senders, tenant_id, conversation_id, run_id, decision.reply, mode)


async def _confirm_no(
    factory: Factory,
    senders: Senders,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    row: convstate.ConvRow,
    final: State,
    mode: str,
) -> None:
    if row.pending_action_id is not None:
        await writes.close_pending(
            factory, tenant_id, row.pending_action_id, "rejected", "CUSTOMER_DECLINED"
        )
    row.awaiting, row.pending_action_id, row.unclear_count = "none", None, 0
    row.context.pop("confirm_token", None)
    await _save(factory, tenant_id, conversation_id, row)
    body = t.render("declined", _lang(final))
    await engine.submit_reply(
        factory,
        senders,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        body=body,
        key=f"{run_id}:reply_customer:1",
        mode=mode,
    )


async def _confirm_yes(
    factory: Factory,
    senders: Senders,
    mailer: Mailer,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    row: convstate.ConvRow,
    final: State,
    policy: PolicyV2,
    box: V1ToolBox,
    mode: str,
) -> None:
    action_id = row.pending_action_id
    token = str(row.context.get("confirm_token") or "")
    async with session_scope(factory, tenant_id=tenant_id) as session:
        action = await writes.get_action(session, action_id) if action_id else None
    if action is None or mode != "auto":
        await _expired(factory, senders, tenant_id, conversation_id, run_id, row, final, mode)
        return
    facts = ToolBoxFacts(box, final.get("customer_phone"), final.get("customer_email"))
    decision = await regate(factory, tenant_id, facts, policy, action)
    if decision.route == "handoff":
        await writes.close_pending(
            factory, tenant_id, action.id, "rejected", f"REGATE:{decision.reason}"
        )
        reason = decision.handoff_reason or "POLICY_EXCEPTION"
        handoff = handoff_decision(final, reason)
        await _handoff(
            factory, senders, mailer, tenant_id, conversation_id, run_id, row, handoff, mode
        )
        return
    try:
        async with session_scope(factory, tenant_id=tenant_id) as session:
            await session.execute(
                text(
                    "UPDATE agent_actions SET requires_approval = (requires_approval OR :r) WHERE id = :i"
                ),
                {"r": decision.route == "approval", "i": action.id},
            )
            confirmed = await writes.confirm(session, action.id, token)
            row.write_count += 1
            row.awaiting, row.pending_action_id, row.unclear_count = "none", None, 0
            row.context.pop("confirm_token", None)
            await convstate.save(session, conversation_id, row)
    except AppError:
        await _expired(factory, senders, tenant_id, conversation_id, run_id, row, final, mode)
        return
    if confirmed.status == "approved":
        await writes.execute(
            factory,
            await get_writer_for_tenant(factory, tenant_id),
            tenant_id,
            action.id,
            policy,
            enforce_limits=True,
        )
    await outcome.notify(factory, senders, mailer, tenant_id, action.id, mode)


async def _expired(
    factory: Factory,
    senders: Senders,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    row: convstate.ConvRow,
    final: State,
    mode: str,
) -> None:
    row.awaiting, row.pending_action_id, row.unclear_count = "none", None, 0
    row.context.pop("confirm_token", None)
    await _save(factory, tenant_id, conversation_id, row)
    await engine.submit_reply(
        factory,
        senders,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        body=t.render("confirm_expired", _lang(final)),
        key=f"{run_id}:reply_customer:1",
        mode=mode,
    )


def _versions(plan: dict[str, Any], final: State) -> dict[str, str]:
    versions: dict[str, str] = {}
    if plan:
        versions["plan"] = PLAN_VERSION
    if final.get("reply_attempts"):
        versions["reply"] = REPLY_VERSION
    return versions


async def _persist(
    session: AsyncSession,
    run_id: uuid.UUID,
    event_id: uuid.UUID,
    conversation_id: uuid.UUID,
    final: State,
    decision: DecisionV1,
    policy_version: int | None,
    mode: str,
    tool_calls: list[dict[str, Any]],
) -> None:
    # PERFORMANCE FIX (2026-11-21): was its own session_scope() checkout - see run()'s
    # comment where this now shares one session with _link_identities_from_order and
    # _load_row right after it (3 checkouts collapsed to 1 on a DB/network path where each
    # checkout alone has been measured costing several hundred ms to multiple seconds).
    # Still used standalone nowhere else (verified: no other caller in app/ or tests/), so
    # this signature change is self-contained to this file.
    identity = final.get("identity", {})
    plan = final.get("plan") or {}
    stored = decision.model_dump(mode="json")
    stored["order_ref"] = identity.get("order_ref") if identity.get("verified") else None
    stored["intent"] = plan.get("intent")
    skipped = decision.action == "skip"
    await session.execute(
        text(
            "UPDATE agent_runs SET decision = CAST(:d AS jsonb), trace = CAST(:t AS jsonb),"
            " status = :s, policy_version = :p, mode = :m, graph_version = :g,"
            " plan = CAST(:pl AS jsonb), prompt_versions = CAST(:pv AS jsonb), reply_attempts = :ra,"
            " finished_at = now() WHERE id = :i"
        ),
        {
            "d": json.dumps(stored, default=str),
            "t": json.dumps(final.get("trace", []), default=str),
            "s": "skipped" if skipped else "done",
            "p": policy_version,
            "m": mode,
            "g": GRAPH_VERSION_V1,
            "pl": json.dumps(plan) if plan else None,
            "pv": json.dumps(_versions(plan, final)),
            "ra": int(final.get("reply_attempts", 0)),
            "i": run_id,
        },
    )
    await engine.record_tool_calls(session, run_id, conversation_id, tool_calls)
    await session.execute(
        text(
            "UPDATE conversations SET identity_attempts = :a,"
            " active_order_ref = CASE WHEN :v THEN :ref ELSE active_order_ref END,"
            " language = COALESCE(CAST(:lang AS text), language) WHERE id = :c"
        ),
        {
            "a": int(identity.get("attempts", 0)),
            "v": bool(identity.get("verified")),
            "ref": identity.get("order_ref"),
            "lang": plan.get("language"),
            "c": conversation_id,
        },
    )
    if skipped:
        await session.execute(
            text("UPDATE inbound_events SET status = 'skipped', skip_reason = :r WHERE id = :i"),
            {"r": decision.reason_code, "i": event_id},
        )
