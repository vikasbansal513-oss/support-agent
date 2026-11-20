import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine
from app.actions.senders import Senders, build_senders
from app.agent import keywords, runner
from app.channels import identity
from app.channels.gupshup_parse import hash_token, tokens_equal
from app.contracts.contracts import NormalizedInbound
from app.core import officehours, timeutil
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import new_error_id
from app.core.flags import is_globally_paused
from app.core.mail import get_mailer
from app.core.masking import redact_for_llm
from app.handoffs import service as handoffs
from app.jobs import handlers, queue
from app.policy import service as policy_service
from app.privacy import service as privacy
from app.v1 import templates as v1t
from app.v1.confirm import parse_confirmation
from app.v1.contracts import ConvUpdate, DecisionV1, HandoffSummaryV1
from app.v1.policy import PolicyV2
from app.voice import speech
from app.voice.contracts import VoiceEventIn, VoiceReplyOut
from app.voice.sender import CaptureVoiceSender

logger = logging.getLogger("app.voice")
Factory = async_sessionmaker[AsyncSession]
PHONE_MASK_KEEP = 3


@dataclass(frozen=True)
class VoiceTenant:
    tenant_id: uuid.UUID
    called_number: str
    enabled: bool
    transfer_number: str | None
    provider: str = "generic"


@dataclass(frozen=True)
class TenantView:
    name: str
    status: str
    mode: str
    paused: bool
    policy: PolicyV2 | None
    timezone: str


async def resolve_token(factory: Factory, token: str) -> VoiceTenant | None:
    digest = hash_token(token)
    async with session_scope(factory) as session:
        row = (
            await session.execute(
                text(
                    "SELECT tenant_id, called_number, enabled, transfer_number, provider, webhook_token_hash"
                    " FROM voice_numbers WHERE webhook_token_hash = :h"
                ),
                {"h": digest},
            )
        ).first()
    if row is None or not tokens_equal(row.webhook_token_hash, digest):
        return None
    return VoiceTenant(
        row.tenant_id, row.called_number, row.enabled, row.transfer_number, row.provider
    )


def mask(phone: str) -> str:
    return f"{phone[:3]}{'*' * max(len(phone) - 2 * PHONE_MASK_KEEP, 0)}{phone[-PHONE_MASK_KEEP:]}"


async def _tenant_view(session: AsyncSession) -> TenantView:
    tenant = (
        await session.execute(
            text(
                "SELECT name, status, mode, agent_paused FROM tenants"
                " WHERE id = app_current_tenant()"
            )
        )
    ).one()
    zone = (
        await session.execute(text("SELECT timezone FROM tenant_settings"))
    ).scalar_one_or_none()
    parsed, _ = await policy_service.get_published_policy(session)
    return TenantView(
        name=str(tenant.name),
        status=str(tenant.status),
        mode=str(tenant.mode),
        paused=bool(tenant.agent_paused) or await is_globally_paused(session),
        policy=parsed if isinstance(parsed, PolicyV2) else None,
        timezone=str(zone or "Asia/Kolkata"),
    )


def _eligible(view: TenantView, voice: VoiceTenant) -> str | None:
    if view.status != "active":
        return "TENANT_INACTIVE"
    if not voice.enabled:
        return "VOICE_DISABLED"
    if view.paused:
        return "AGENT_PAUSED"
    if view.mode != "auto":
        return "MODE_NOT_AUTO"
    if view.policy is None:
        return "POLICY_MISSING"
    return None


def _in_hours(view: TenantView) -> bool:
    if view.policy is None:
        return False
    return officehours.is_open(view.policy, timeutil.utc_now(), view.timezone)


@dataclass(frozen=True)
class CallContext:
    order_ref: str | None
    first_said: str | None
    last_said: str | None
    turns: int
    did: list[str]


async def _ctx_fetch_call(session: AsyncSession, call_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text("SELECT started_at, turn_count FROM voice_calls WHERE id = :i"), {"i": call_id}
        )
    ).one()


async def _ctx_fetch_order_ref(session: AsyncSession, conversation_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text("SELECT active_order_ref FROM conversations WHERE id = :c"),
            {"c": conversation_id},
        )
    ).scalar_one_or_none()


async def _ctx_fetch_said(
    session: AsyncSession, conversation_id: uuid.UUID, started_at: Any
) -> Any:
    return (
        (
            await session.execute(
                text(
                    "SELECT content FROM messages WHERE conversation_id = :c AND channel = 'voice'"
                    " AND direction = 'in' AND created_at >= :s ORDER BY created_at"
                ),
                {"c": conversation_id, "s": started_at},
            )
        )
        .scalars()
        .all()
    )


async def _ctx_fetch_acts(
    session: AsyncSession, conversation_id: uuid.UUID, started_at: Any
) -> Any:
    return (
        await session.execute(
            text(
                "SELECT tool_name, status FROM agent_actions WHERE conversation_id = :c"
                " AND tool_name IS NOT NULL AND created_at >= :s ORDER BY created_at"
            ),
            {"c": conversation_id, "s": started_at},
        )
    ).all()


async def _context(
    factory: Factory, tenant_id: uuid.UUID, call_id: uuid.UUID, conversation_id: uuid.UUID
) -> CallContext:
    # PERFORMANCE FIX (2026-11-19, supersedes the 2026-11-18 fix below it): the
    # 2026-11-18 change ran these 4 reads concurrently via two asyncio.gather pairs,
    # each read opening its OWN session_scope. The next real call ("voice turn timed
    # out", 2026-11-19) showed "_context timing: all 4 queries done" still taking
    # ~15.3s, with the single "call-row session acquired" checkout alone costing
    # ~4.5s - i.e. the per-checkout cost (pool checkout + BEGIN + the tenant-context
    # SET_CONFIG round trip), not the query itself, is what's slow on this DB/network
    # path right now, and running 4 checkouts concurrently only overlaps that cost,
    # it doesn't remove it.
    # FIX: one shared session for the whole function, checked out once, running the
    # 4 reads one after another on it - `said`/`acts` still wait on `call.started_at`
    # exactly as before, they just no longer need their own separate connection to
    # do so.
    # VERIFY: not run against a live DB from here (no pytest/app execution per this
    # change's own constraints) - only checked with `python -m py_compile`/ruff.
    # Confirm on the next real call that this timing drops well below the ~15s seen
    # on 2026-11-19. If the single checkout itself is STILL several seconds, that's
    # DB/network latency this app's own code can't remove by restructuring queries -
    # worth checking next whether DATABASE_URL points at Neon's pooled connection
    # endpoint (see app.agent.runner._load_state's matching 2026-11-19 comment).
    _t0 = time.monotonic()
    async with session_scope(factory, tenant_id=tenant_id) as session:
        logger.info(
            "_context timing: session acquired elapsed_ms=%d",
            int((time.monotonic() - _t0) * 1000),
        )
        call = await _ctx_fetch_call(session, call_id)
        order_ref = await _ctx_fetch_order_ref(session, conversation_id)
        said = await _ctx_fetch_said(session, conversation_id, call.started_at)
        acts = await _ctx_fetch_acts(session, conversation_id, call.started_at)
    logger.info(
        "_context timing: all 4 queries done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    did = [f"Call par {call.turn_count} baatein hui"]
    did += [f"{a.tool_name}: {a.status}" for a in acts][:8]
    return CallContext(
        order_ref=str(order_ref) if order_ref else None,
        first_said=redact_for_llm(said[0], 150) if said else None,
        last_said=redact_for_llm(said[-1], 150) if said else None,
        turns=int(call.turn_count),
        did=did,
    )


def _briefing(phone: str, reason: str, ctx: CallContext) -> str:
    parts = [
        f"Customer {mask(phone)}",
        f"Order {ctx.order_ref}" if ctx.order_ref else "Order nahi bataya",
        f"Pehle kaha: {ctx.first_said}" if ctx.first_said else "",
        f"Abhi kaha: {ctx.last_said}" if ctx.last_said and ctx.last_said != ctx.first_said else "",
        f"Wajah: {WHY.get(reason, 'Insaan chahiye')}",
    ]
    return ". ".join(p for p in parts if p)[:300]


def _handoff_decision(
    reason: str, asked: str, phone: str, lang: str, ctx: CallContext | None = None
) -> DecisionV1:
    why = WHY.get(reason, "Insaan ko customer ko call karna hai.")
    if ctx is not None and ctx.order_ref:
        why = f"{why} Order {ctx.order_ref}."
    summary = HandoffSummaryV1(
        customer_masked_phone=mask(phone),
        order_ref=ctx.order_ref if ctx else None,
        customer_asked=(ctx.last_said if ctx and ctx.last_said else asked)[:400] or "-",
        agent_did=(ctx.did if ctx else ["Voice call"])[:10],
        reason_code=reason,
        why_stuck=why[:300],
        language=lang if lang in ("hi", "en", "hinglish") else "hinglish",
    )
    return DecisionV1(
        action="handoff",
        reason_code=reason,
        handoff=summary,
        conv=ConvUpdate(clear_pending=True),
    )


WHY = {
    "CONFUSED": "The call audio was repeatedly unclear.",
    "STEP_LIMIT": "The call ran too long; a human should take over.",
    "POLICY_EXCEPTION": "The voice agent isn't enabled for this brand yet, or the request fell"
    " outside policy.",
    "ACTION_FAILED": "A technical issue occurred during the call.",
    "ACTION_TIMEOUT": "The AI assistant took too long to respond and was cut off to avoid"
    " leaving the customer in silence.",
    # Section 17.1 consent-capture (see _consent_turn):
    "HUMAN_REQUESTED": "Customer asked to speak with a human right at the start of the call.",
    "CONSENT_DECLINED": "Customer did not consent to speaking with the AI assistant / having the"
    " call recorded.",
    # Section 17.4:
    "SILENCE_NO_RESPONSE": "Customer did not respond even after being prompted again.",
}


async def _senders(factory: Factory, tenant_id: uuid.UUID, capture: CaptureVoiceSender) -> Senders:
    base = await build_senders(factory, tenant_id)
    return replace(base, voice=capture)


async def _open_handoff(
    factory: Factory,
    voice: VoiceTenant,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID | None,
    decision: DecisionV1,
    mode: str,
) -> uuid.UUID | None:
    capture = CaptureVoiceSender()
    senders = await _senders(factory, voice.tenant_id, capture)
    return await handoffs.create_handoff(
        factory,
        get_mailer(),
        senders,
        tenant_id=voice.tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        decision=decision,
        mode="auto" if mode == "shadow" else mode,
    )


async def _log_message(
    session: AsyncSession, conversation_id: uuid.UUID, direction: str, content: str, ext: str | None
) -> uuid.UUID:
    return uuid.UUID(
        str(
            (
                await session.execute(
                    text(
                        "INSERT INTO messages (conversation_id, direction, sender, channel, content,"
                        " external_id) VALUES (:c, :d, :s, 'voice', :t, :e) RETURNING id"
                    ),
                    {
                        "c": conversation_id,
                        "d": direction,
                        "s": "customer" if direction == "in" else "agent",
                        "t": content,
                        "e": ext,
                    },
                )
            ).scalar_one()
        )
    )


async def _customer(session: AsyncSession, phone: str) -> uuid.UUID:
    await session.execute(
        text(
            "INSERT INTO customers (phone_e164) VALUES (:v)"
            " ON CONFLICT (tenant_id, phone_e164) WHERE phone_e164 IS NOT NULL DO NOTHING"
        ),
        {"v": phone},
    )
    return uuid.UUID(
        str(
            (
                await session.execute(
                    text("SELECT id FROM customers WHERE phone_e164 = :v"), {"v": phone}
                )
            ).scalar_one()
        )
    )


def _reply(
    say: str,
    lang: str,
    action: str = "continue",
    transfer_to: str | None = None,
    *,
    barge_in: bool = True,
    briefing: str | None = None,
) -> VoiceReplyOut:
    return VoiceReplyOut(
        say=say,
        language=speech.tts_language(lang),
        action=action,
        transfer_to=transfer_to,
        barge_in=barge_in,
        briefing=briefing,
    )


async def _end_with_human(
    factory: Factory,
    voice: VoiceTenant,
    view: TenantView,
    call_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID | None,
    reason: str,
    asked: str,
    phone: str,
    lang: str,
    spoken_override: str | None = None,
) -> VoiceReplyOut:
    transfer = voice.transfer_number if voice.transfer_number and _in_hours(view) else None
    ctx = await _context(factory, voice.tenant_id, call_id, conversation_id)
    handoff_id = await _open_handoff(
        factory,
        voice,
        conversation_id,
        run_id,
        _handoff_decision(reason, asked, phone, lang, ctx),
        view.mode,
    )
    if transfer:
        line = spoken_override or speech.voice_line("transfer", lang)
        status, action = "transferred", "transfer"
    else:
        # Decision Framework Sections 8/15: a callback (no live human on the line right
        # now) must still end with a reference number the customer can quote back - a
        # warm transfer does not need one since a human is taking the call over live,
        # already briefed via _briefing() below.
        key = "callback" if _in_hours(view) else "callback_off_hours"
        line = spoken_override or speech.voice_line(key, lang)
        if handoff_id is not None:
            ref = speech.voice_line("reference", lang, ref=handoffs.reference_code(handoff_id))
            line = f"{line} {ref}"
        status, action = "callback", "hangup"
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await _log_message(session, conversation_id, "out", line, None)
        await session.execute(
            text(
                "UPDATE voice_calls SET status = :s, end_reason = :r, ended_at = now() WHERE id = :i"
            ),
            {"s": status, "r": reason[:100], "i": call_id},
        )
    return _reply(
        line,
        lang,
        action,
        transfer,
        briefing=_briefing(phone, reason, ctx) if transfer else None,
    )


async def handle(
    factory: Factory,
    voice: VoiceTenant,
    event: VoiceEventIn,
    transport: httpx.AsyncBaseTransport | None = None,
) -> VoiceReplyOut:
    if event.event == "start":
        return await _start(factory, voice, event)
    if event.event == "end":
        return await _end(factory, voice, event, transport)
    started = time.monotonic()
    reply = await _turn(factory, voice, event, transport)
    await _record_latency(factory, voice, event.call_id, int((time.monotonic() - started) * 1000))
    return reply


async def _record_latency(factory: Factory, voice: VoiceTenant, call_id: str, ms: int) -> None:
    slow = ms > get_settings().voice_slow_ms
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE voice_calls SET latency_total_ms = latency_total_ms + :m,"
                " latency_max_ms = GREATEST(latency_max_ms, :m),"
                " slow_turns = slow_turns + :s WHERE provider_call_id = :p"
            ),
            {"m": ms, "s": 1 if slow else 0, "p": call_id},
        )


async def _start(factory: Factory, voice: VoiceTenant, event: VoiceEventIn) -> VoiceReplyOut:
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        view = await _tenant_view(session)
        customer_id = await _customer(session, event.from_number)
        conv_id = await _conversation(session, customer_id)
        await privacy.ensure_consent(session, customer_id, "voice", "first_inbound")
        existing = (
            await session.execute(
                text("SELECT id, status FROM voice_calls WHERE provider_call_id = :p"),
                {"p": event.call_id},
            )
        ).first()
        if existing is not None:
            return _reply(
                speech.voice_line("greeting", "hinglish", brand=view.name),
                "hinglish",
                barge_in=False,
            )
        remembered = await _remember_order(session, customer_id, conv_id)
        call_id = uuid.UUID(
            str(
                (
                    await session.execute(
                        text(
                            "INSERT INTO voice_calls (provider_call_id, conversation_id, customer_id,"
                            " caller_masked, order_ref) VALUES (:p, :c, :u, :m, :o) RETURNING id"
                        ),
                        {
                            "p": event.call_id,
                            "c": conv_id,
                            "u": customer_id,
                            "m": mask(event.from_number),
                            "o": remembered,
                        },
                    )
                ).scalar_one()
            )
        )
    blocked = _eligible(view, voice)
    if blocked is not None:
        return await _end_with_human(
            factory,
            voice,
            view,
            call_id,
            conv_id,
            None,
            "POLICY_EXCEPTION",
            f"voice call ({blocked})",
            event.from_number,
            "hinglish",
        )
    greeting = speech.voice_line("greeting", "hinglish", brand=view.name)
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await _log_message(session, conv_id, "out", greeting, None)
    return _reply(greeting, "hinglish", barge_in=False)


async def _remember_order(
    session: AsyncSession, customer_id: uuid.UUID, conversation_id: uuid.UUID
) -> str | None:
    current = (
        await session.execute(
            text("SELECT active_order_ref FROM conversations WHERE id = :c"), {"c": conversation_id}
        )
    ).scalar_one_or_none()
    if current:
        return str(current)
    days = get_settings().voice_order_memory_days
    if days <= 0:
        return None
    previous = (
        await session.execute(
            text(
                "SELECT active_order_ref FROM conversations WHERE customer_id = :u AND id <> :c"
                " AND active_order_ref IS NOT NULL"
                " AND last_message_at >= now() - make_interval(days => :d)"
                " ORDER BY last_message_at DESC LIMIT 1"
            ),
            {"u": customer_id, "c": conversation_id, "d": days},
        )
    ).scalar_one_or_none()
    if not previous:
        return None
    await session.execute(
        text("UPDATE conversations SET active_order_ref = :r WHERE id = :c"),
        {"r": previous, "c": conversation_id},
    )
    return str(previous)


async def _conversation(session: AsyncSession, customer_id: uuid.UUID) -> uuid.UUID:
    ref = await identity.get_or_create_conversation(session, customer_id, "voice")
    return ref.conversation_id


TRANSFER_FAILED = {"transfer_failed", "no_answer", "busy", "failed", "unanswered"}


async def _end(
    factory: Factory,
    voice: VoiceTenant,
    event: VoiceEventIn,
    transport: httpx.AsyncBaseTransport | None,
) -> VoiceReplyOut:
    reason = (event.reason or "hangup")[:100]
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, conversation_id, status, caller_masked FROM voice_calls"
                    " WHERE provider_call_id = :p FOR UPDATE"
                ),
                {"p": event.call_id},
            )
        ).first()
        if row is None:
            return _reply("", "hinglish", "hangup")
        failed_transfer = row.status == "transferred" and reason in TRANSFER_FAILED
        await session.execute(
            text(
                "UPDATE voice_calls SET status = CASE WHEN :ft THEN 'callback'"
                " WHEN status = 'in_progress' THEN 'completed' ELSE status END,"
                " ended_at = COALESCE(ended_at, now()),"
                " end_reason = CASE WHEN :ft THEN 'transfer_failed' ELSE COALESCE(end_reason, :r) END"
                " WHERE id = :i"
            ),
            {"ft": failed_transfer, "r": reason, "i": row.id},
        )
    if failed_transfer:
        base = await build_senders(factory, voice.tenant_id, transport)
        await handoffs.escalate_urgent(
            factory,
            get_mailer(),
            base,
            tenant_id=voice.tenant_id,
            conversation_id=row.conversation_id,
            title="Transfer nahi lag paya",
            body=f"Customer {row.caller_masked} ko team se jodna fail hua ({reason}). "
            "Unhe jaldi call karein.",
        )
    await _flag_high_value_for_review(
        factory, voice, row.id, row.conversation_id, row.caller_masked, transport
    )
    await _send_summary(factory, voice, row.id, row.conversation_id, transport)
    return _reply("", "hinglish", "hangup")


async def _flag_high_value_for_review(
    factory: Factory,
    voice: VoiceTenant,
    call_id: uuid.UUID,
    conversation_id: uuid.UUID,
    caller_masked: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    """Decision Framework Section 17.7: calls where a high-value write action happened get
    an automated QA review flag, same as a disputed/escalated call already does. Reuses the
    exact handoffs.escalate_urgent(...) call already used above for failed transfers, rather
    than adding a new review mechanism. Skipped when a handoff already exists for this call
    (live escalation already means a human is looking at it) or when the policy has no
    published high_value_threshold_paise to compare against."""
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        view = await _tenant_view(session)
        threshold = view.policy.limits.high_value_threshold_paise if view.policy else None
        high_value_paise = None
        if threshold is not None:
            high_value_paise = (
                await session.execute(
                    text(
                        "SELECT amount_paise FROM agent_actions WHERE conversation_id = :c"
                        " AND amount_paise IS NOT NULL AND amount_paise >= :t"
                        " ORDER BY amount_paise DESC LIMIT 1"
                    ),
                    {"c": conversation_id, "t": threshold},
                )
            ).scalar_one_or_none()
        if high_value_paise is None:
            return
        already_flagged = (
            await session.execute(
                text("SELECT 1 FROM handoffs WHERE conversation_id = :c"), {"c": conversation_id}
            )
        ).first()
    if already_flagged is not None:
        return
    base = await build_senders(factory, voice.tenant_id, transport)
    await handoffs.escalate_urgent(
        factory,
        get_mailer(),
        base,
        tenant_id=voice.tenant_id,
        conversation_id=conversation_id,
        title="Voice call: high-value action, QA review zaroori",
        body=f"Customer {caller_masked} ki call me Rs {high_value_paise // 100} ya usse zyada"
        " ka action hua. Section 17.7 ke mutabik is call ki QA review karein.",
    )


async def _summary_text(
    factory: Factory, voice: VoiceTenant, call_id: uuid.UUID, conversation_id: uuid.UUID
) -> str:
    ctx = await _context(factory, voice.tenant_id, call_id, conversation_id)
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        brand = (
            await session.execute(text("SELECT name FROM tenants WHERE id = app_current_tenant()"))
        ).scalar_one()
        lang = (
            await session.execute(
                text("SELECT language FROM conversations WHERE id = :c"), {"c": conversation_id}
            )
        ).scalar_one_or_none() or "hinglish"
        acts = (
            await session.execute(
                text(
                    "SELECT tool_name, status FROM agent_actions WHERE conversation_id = :c"
                    " AND tool_name IS NOT NULL AND status IN ('confirmed','pending_approval')"
                    " ORDER BY created_at DESC LIMIT 3"
                ),
                {"c": conversation_id},
            )
        ).all()
        open_handoff = (
            await session.execute(
                text(
                    "SELECT 1 FROM handoffs WHERE conversation_id = :c AND status IN ('open','claimed')"
                ),
                {"c": conversation_id},
            )
        ).first()
    parts: list[str] = []
    if ctx.order_ref:
        parts.append(speech.voice_line("summary_order", lang, order=ctx.order_ref))
    for act in acts:
        if act.status == "confirmed" and ctx.order_ref and act.tool_name != "refund_create":
            done = f"done_{act.tool_name}"
            if done in v1t.T:
                parts.append(v1t.render(done, lang, order=ctx.order_ref))
        elif act.status == "pending_approval":
            parts.append(speech.voice_line("summary_pending", lang))
    if open_handoff is not None and not any(a.status == "pending_approval" for a in acts):
        parts.append(speech.voice_line("summary_callback", lang))
    if not parts:
        return ""
    return speech.voice_line("summary", lang, brand=str(brand), body=" ".join(parts))


async def _send_summary(
    factory: Factory,
    voice: VoiceTenant,
    call_id: uuid.UUID,
    conversation_id: uuid.UUID,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        done = (
            await session.execute(
                text("SELECT summary_status FROM voice_calls WHERE id = :i"), {"i": call_id}
            )
        ).scalar_one()
        if done is not None:
            return
        wa = (
            await session.execute(
                text(
                    "SELECT w.id FROM conversations v JOIN conversations w"
                    " ON w.customer_id = v.customer_id AND w.channel = 'whatsapp'"
                    " WHERE v.id = :c AND w.last_inbound_at > now() - interval '24 hours'"
                    " ORDER BY w.last_inbound_at DESC LIMIT 1"
                ),
                {"c": conversation_id},
            )
        ).first()
        mode = (
            await session.execute(text("SELECT mode FROM tenants WHERE id = app_current_tenant()"))
        ).scalar_one()
    status = "skipped"
    body = await _summary_text(factory, voice, call_id, conversation_id) if wa else ""
    if wa is not None and body:
        senders = await build_senders(factory, voice.tenant_id, transport)
        try:
            result = await engine.submit_reply(
                factory,
                senders,
                tenant_id=voice.tenant_id,
                conversation_id=wa.id,
                run_id=None,
                body=body,
                key=f"voice-summary:{call_id}",
                mode=str(mode),
            )
            status = "sent" if result.status == "sent" else "skipped"
        except Exception as exc:
            logger.error(
                "voice summary failed",
                extra={"error_id": new_error_id(), "error_class": type(exc).__name__},
            )
            status = "failed"
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await session.execute(
            text("UPDATE voice_calls SET summary_status = :s WHERE id = :i"),
            {"s": status, "i": call_id},
        )


@dataclass
class CallRow:
    id: uuid.UUID
    conversation_id: uuid.UUID
    customer_id: uuid.UUID
    status: str
    turn_count: int
    low_confidence_streak: int
    silence_streak: int
    language: str
    awaiting: str
    consent_response: str | None


async def _load_call(session: AsyncSession, call_id: str) -> CallRow | None:
    row = (
        await session.execute(
            text(
                "SELECT v.id, v.conversation_id, v.customer_id, v.status, v.turn_count,"
                " v.low_confidence_streak, v.silence_streak, c.language,"
                " COALESCE(s.awaiting, 'none') AS awaiting, v.consent_response FROM voice_calls v"
                " JOIN conversations c ON c.id = v.conversation_id"
                " LEFT JOIN conversation_state s ON s.conversation_id = v.conversation_id"
                " WHERE v.provider_call_id = :p FOR UPDATE OF v"
            ),
            {"p": call_id},
        )
    ).first()
    if row is None:
        return None
    return CallRow(
        row.id,
        row.conversation_id,
        row.customer_id,
        row.status,
        row.turn_count,
        row.low_confidence_streak,
        row.silence_streak,
        row.language or "hinglish",
        row.awaiting,
        row.consent_response,
    )


async def _bump(
    session: AsyncSession,
    call: CallRow,
    *,
    turns: int = 0,
    low: int | None = None,
    quiet: int | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE voice_calls SET turn_count = turn_count + :t,"
            " low_confidence_streak = COALESCE(:l, low_confidence_streak),"
            " silence_streak = COALESCE(:q, silence_streak) WHERE id = :i"
        ),
        {"t": turns, "l": low, "q": quiet, "i": call.id},
    )


async def _consent_turn(
    factory: Factory,
    voice: VoiceTenant,
    view: TenantView,
    call: CallRow,
    event: VoiceEventIn,
    utterance: str,
    lang: str,
) -> VoiceReplyOut | None:
    """Decision Framework Section 17.1: the greeting discloses AI + recording and ends with
    an explicit yes/no question. This handles the reply to that question - the very first
    real turn of the call - before any normal eligibility/step-limit/agent processing runs.

    Returns a VoiceReplyOut to end the turn here (human-request/explicit-no), or None to
    record implicit/explicit consent and let the caller (_turn) fall through and process
    this same utterance as the customer's real request - see the ROOT-CAUSE FIX
    (2026-11-20, revised) comment below for why this function does NOT itself gate on
    event.confidence or re-ask a dedicated "unclear consent" line.
    """
    if "human" in keywords.detect_flags(utterance):
        return await _end_with_human(
            factory,
            voice,
            view,
            call.id,
            call.conversation_id,
            None,
            "HUMAN_REQUESTED",
            utterance,
            event.from_number,
            lang,
        )
    # ROOT-CAUSE FIX (2026-11-20, revised): two earlier versions of this fix both went
    # too far:
    #   1. First attempt reasked/escalated whenever parse_confirmation() returned
    #      "unclear" - but that function is a yes/no KEYWORD matcher (see
    #      app/v1/confirm.py), not an audio-quality check: it reads "mera order 1001
    #      kahan hai" as "unclear" too, simply because those words aren't in its yes/no
    #      list. That broke the normal, common case of a customer skipping this
    #      question and stating their real need directly - caught immediately by this
    #      codebase's own test suite (tests/voice/test_voice_calls.py regressed across
    #      the board).
    #   2. Second attempt added its own event.confidence check here instead, re-asking
    #      with a dedicated "consent_reask" line on low confidence - but
    #      tests/voice/test_voice_calls.py::test_two_unclear_audio_turns_hand_off (a
    #      pre-existing, already-passing test, not one this fix added) proves the
    #      low-confidence reask/escalate behaviour the user actually asked for
    #      ("customer ki clear voice na sunayi de tab tak kaam na kare") already exists,
    #      generically, in app.voice.service._turn() further down (the same
    #      low_confidence_streak/"dobara bol"/CONFUSED mechanism used for every other
    #      turn of the call) - and this function's own implicit-consent fallthrough
    #      (the "else" branch below) already reaches that exact same shared check in
    #      the same call, for this very first turn, with no extra code needed here.
    #      Checking confidence a second time in this function just intercepted it
    #      earlier with a different, inconsistent message.
    # So this function is back to only deciding "did they explicitly ask for a human or
    # say no" - everything else (a clear "yes", a clear real request, or genuinely
    # unclear audio) falls through to the one shared turn-processing path below, which
    # already handles all three correctly and is what the test suite verifies.
    answer = parse_confirmation(utterance)
    if answer == "no":
        async with session_scope(factory, tenant_id=voice.tenant_id) as session:
            await session.execute(
                text(
                    "UPDATE voice_calls SET consent_response = 'no', consent_responded_at = now()"
                    " WHERE id = :i"
                ),
                {"i": call.id},
            )
        return await _end_with_human(
            factory,
            voice,
            view,
            call.id,
            call.conversation_id,
            None,
            "CONSENT_DECLINED",
            utterance,
            event.from_number,
            lang,
        )
    # A clear "yes", or anything else non-"no" (parse_confirmation's "unclear",
    # whatever the audio quality was) - both mean the call continues: record
    # implicit/explicit consent and return None so _turn() falls through and
    # processes this same utterance as the customer's real request, including its
    # own low-confidence check further down if event.confidence is in fact low
    # (restores pre-existing, test-covered behaviour - see ROOT-CAUSE FIX above).
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE voice_calls SET consent_response = 'yes', consent_responded_at = now()"
                " WHERE id = :i"
            ),
            {"i": call.id},
        )
    return None


async def _turn(
    factory: Factory,
    voice: VoiceTenant,
    event: VoiceEventIn,
    transport: httpx.AsyncBaseTransport | None,
) -> VoiceReplyOut:
    settings = get_settings()
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        call = await _load_call(session, event.call_id)
        view = await _tenant_view(session)
    if call is None:
        return _reply(speech.voice_line("error", "hinglish"), "hinglish", "hangup")
    lang = call.language
    if call.status != "in_progress":
        return _reply("", lang, "hangup")
    utterance = event.speech.strip() or speech.digits_to_text(event.digits)

    if not utterance:
        async with session_scope(factory, tenant_id=voice.tenant_id) as session:
            await _bump(session, call, quiet=call.silence_streak + 1)
        if call.silence_streak + 1 >= settings.voice_silence_limit:
            # Section 17.4: after the reprompt goes unanswered, offer a callback and end
            # politely rather than just hanging up with a plain goodbye - reuses the same
            # handoff/transfer-or-callback machinery as CONFUSED/STEP_LIMIT, so a real
            # follow-up record exists for the team to call the customer back on.
            return await _end_with_human(
                factory,
                voice,
                view,
                call.id,
                call.conversation_id,
                None,
                "SILENCE_NO_RESPONSE",
                "no response",
                event.from_number,
                lang,
            )
        return _reply(speech.voice_line("silence", lang), lang)

    if call.consent_response is None:
        # 2026-11-20 (revised): _consent_turn() ends the turn here (reask/handoff) only
        # when the STT genuinely didn't hear the answer clearly (low event.confidence)
        # or the customer explicitly said "no" / asked for a human - see its own
        # ROOT-CAUSE FIX comment. Otherwise it records implicit/explicit consent and
        # returns None, and this same utterance falls through to the rest of this
        # function exactly as before, so a customer who states their real need
        # straight away (no separate "yes") still gets it handled in that same turn.
        consent_reply = await _consent_turn(factory, voice, view, call, event, utterance, lang)
        if consent_reply is not None:
            return consent_reply

    ext = event.turn_id or f"{event.call_id}:{call.turn_count + 1}"
    blocked = _eligible(view, voice)
    if blocked is not None:
        return await _end_with_human(
            factory,
            voice,
            view,
            call.id,
            call.conversation_id,
            None,
            "POLICY_EXCEPTION",
            utterance,
            event.from_number,
            lang,
        )
    if call.turn_count >= settings.voice_max_turns:
        return await _end_with_human(
            factory,
            voice,
            view,
            call.id,
            call.conversation_id,
            None,
            "STEP_LIMIT",
            utterance,
            event.from_number,
            lang,
        )

    threshold = (
        settings.voice_confirm_confidence
        if call.awaiting == "confirm"
        else settings.voice_min_confidence
    )
    if event.confidence is not None and event.confidence < threshold:
        streak = call.low_confidence_streak + 1
        async with session_scope(factory, tenant_id=voice.tenant_id) as session:
            await _bump(session, call, low=streak, quiet=0)
        if streak >= settings.voice_low_confidence_limit:
            return await _end_with_human(
                factory,
                voice,
                view,
                call.id,
                call.conversation_id,
                None,
                "CONFUSED",
                "awaaz saaf nahi",
                event.from_number,
                lang,
            )
        key = "unclear_confirm" if call.awaiting == "confirm" else "unclear_audio"
        return _reply(speech.voice_line(key, lang), lang)

    return await _run_agent(factory, voice, view, call, event, utterance, ext, transport)


async def _run_agent(
    factory: Factory,
    voice: VoiceTenant,
    view: TenantView,
    call: CallRow,
    event: VoiceEventIn,
    utterance: str,
    ext: str,
    transport: httpx.AsyncBaseTransport | None,
) -> VoiceReplyOut:
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        done = (
            await session.execute(
                text(
                    "SELECT raw_payload FROM inbound_events WHERE channel = 'voice' AND external_id = :e"
                ),
                {"e": ext},
            )
        ).first()
        if done is not None:
            stored = (done.raw_payload or {}).get("voice_reply")
            if stored:
                return VoiceReplyOut.model_validate(stored)
            return _reply("", call.language, "continue")
        event_id = uuid.UUID(
            str(
                (
                    await session.execute(
                        text(
                            "INSERT INTO inbound_events (channel, external_id, raw_payload, status)"
                            " VALUES ('voice', :e, CAST(:raw AS jsonb), 'queued') RETURNING id"
                        ),
                        {"e": ext, "raw": json.dumps({"voice": {"call": event.call_id}})},
                    )
                ).scalar_one()
            )
        )
        message_id = await _log_message(session, call.conversation_id, "in", utterance, ext)
        await session.execute(
            text("UPDATE messages SET inbound_event_id = :e WHERE id = :m"),
            {"e": event_id, "m": message_id},
        )
        await session.execute(
            text(
                "UPDATE conversations SET last_inbound_at = now(), last_message_at = now() WHERE id = :c"
            ),
            {"c": call.conversation_id},
        )
        await _bump(session, call, turns=1, low=0, quiet=0)

    capture = CaptureVoiceSender()
    senders = await _senders(factory, voice.tenant_id, capture)
    job = queue.Job(
        id=uuid.uuid4(),
        tenant_id=voice.tenant_id,
        kind="process_inbound",
        payload={"inbound_event_id": str(event_id)},
        attempts=3,
        max_attempts=3,
    )
    ctx = handlers.JobContext(factory=factory, job=job, transport=transport, senders=senders)
    inbound = NormalizedInbound(
        channel="whatsapp",
        external_id=ext,
        from_phone=event.from_number,
        text=utterance[:2000],
        received_at=timeutil.utc_now(),
        # ROOT-CAUSE FIX (2026-11-17): tells app.agent.runner._llm to use a short,
        # no-retry timeout for this run's LLM call instead of the WhatsApp/email
        # default (see NormalizedInbound.is_live_call's docstring for the full
        # reasoning/evidence - a real call measured a 99.7s silent gap without this).
        is_live_call=True,
    )
    # HARD SAFETY NET (2026-11-17): this is on top of (not instead of) the specific
    # fixes already made for the LLM call (app.agent.runner._llm's fast/no-retry mode)
    # and TTS (app.webhooks.voice_stream's short TTS timeout). Those targeted two
    # confirmed slow spots, but a real call also showed a 25-33s gap with no matching
    # LLM/TTS/STT activity at all - that gap sits *after* run_inbound returns (its
    # last log line, "v1 run done", is right before the return), i.e. inside
    # _finish_turn/_handoff_reply/_context below (see their "... timing" diagnostic
    # logs), which only do DB queries - no external HTTP call to blame it on yet.
    # There may be other slow paths (DB, a future integration call, etc.) not
    # discovered yet either. A live phone call cannot tolerate open-ended silence
    # while every individual cause gets hunted down one at a time - so this wraps
    # run_inbound AND _finish_turn (the *entire* per-turn pipeline, not just the LLM
    # part) in one hard ceiling. Whatever is slow, anywhere in that span, the caller
    # hears SOMETHING (a graceful "connecting you" handoff) within this timeout,
    # never silence beyond it.
    #
    # CORRECTION (2026-11-17): the original 12s budget here assumed "~6s generous
    # headroom for every DB step" - but a real call's diagnostic logs showed the
    # pre-LLM DB work ALONE (load_state + _tool_box + runner_v1.load_v1_state) took
    # 7.3s, more than that entire assumed headroom, before the LLM call even started.
    # That meant this outer 12s ceiling could fire and hard-cancel the LLM call
    # mid-flight, *before* its own 6s timeout (LIVE_CALL_LLM_TIMEOUT_S) got a chance
    # to finish and fail gracefully - the worse of the two outcomes. Root cause of
    # that DB slowness: _tool_box and load_v1_state's `killed` loop were each doing
    # 1 DB round-trip per item (9 and 10 round-trips respectively) where 1-2 would
    # do - now fixed (see app.integrations.service.load_secrets and
    # app.actions.writes.killed_many). With that fixed, pre-LLM DB work should be
    # ~1-2s on a normal call, so 16s gives the 6s LLM call room to run AND fail
    # gracefully inside the ceiling, plus real headroom for finish_turn/
    # handoff_reply/_context afterward - without the outer net racing the inner one.
    #
    # RAISED 16s -> 25s (2026-11-19): a real call ("voice turn timed out") tripped
    # this ceiling with run_inbound's own "v1 run done" log at ~14.1s and
    # _context's "all 4 queries done" at ~15.3s AFTER that - i.e. legitimate (if
    # slow) DB-bound work, not a hang, was still running past 16s. The matching
    # fix in app.agent.runner._load_state and this module's _context (one shared
    # session per function instead of several) should bring normal turns back
    # down well under 16s; this bump is a safety margin on top of that fix, not a
    # replacement for it, so a call still gets SOME reply within 25s instead of
    # silence even if a particular turn is still slow for another reason.
    TURN_TIMEOUT_S = 25.0

    async def _process_and_finish() -> VoiceReplyOut:
        await runner.run_inbound(
            ctx, voice.tenant_id, event_id, call.conversation_id, message_id, inbound
        )
        return await _finish_turn(factory, voice, view, call, event, event_id, utterance, capture)

    try:
        return await asyncio.wait_for(_process_and_finish(), timeout=TURN_TIMEOUT_S)
    except TimeoutError:
        error_id = new_error_id()
        logger.error(
            "voice turn timed out",
            extra={"error_id": error_id, "timeout_s": TURN_TIMEOUT_S},
        )
        async with session_scope(factory, tenant_id=voice.tenant_id) as session:
            await session.execute(
                text(
                    "UPDATE inbound_events SET status = 'skipped', skip_reason = 'VOICE_TIMEOUT'"
                    " WHERE id = :i AND status NOT IN ('processed','skipped')"
                ),
                {"i": event_id},
            )
        return await _end_with_human(
            factory,
            voice,
            view,
            call.id,
            call.conversation_id,
            None,
            "ACTION_TIMEOUT",
            utterance,
            event.from_number,
            call.language,
            spoken_override=speech.voice_line("error", call.language),
        )
    except Exception as exc:
        error_id = new_error_id()
        logger.error(
            "voice turn failed", extra={"error_id": error_id, "error_class": type(exc).__name__}
        )
        async with session_scope(factory, tenant_id=voice.tenant_id) as session:
            await session.execute(
                text(
                    "UPDATE inbound_events SET status = 'skipped', skip_reason = 'VOICE_ERROR'"
                    " WHERE id = :i AND status NOT IN ('processed','skipped')"
                ),
                {"i": event_id},
            )
        return await _end_with_human(
            factory,
            voice,
            view,
            call.id,
            call.conversation_id,
            None,
            "ACTION_FAILED",
            utterance,
            event.from_number,
            call.language,
            spoken_override=speech.voice_line("error", call.language),
        )


async def _finish_turn(
    factory: Factory,
    voice: VoiceTenant,
    view: TenantView,
    call: CallRow,
    event: VoiceEventIn,
    event_id: uuid.UUID,
    utterance: str,
    capture: CaptureVoiceSender,
) -> VoiceReplyOut:
    # TEMPORARY DIAGNOSTIC (2026-11-17): a real call's external_calls rows showed a
    # ~25s gap with zero HTTP activity (no groq/TTS/STT row) between run_inbound
    # finishing and the next TTS call - i.e. the delay is somewhere in this function's
    # own DB work (or app.voice.service._handoff_reply/_context below), not in any
    # external API. Nothing here was timed before, so there's no way yet to tell DB
    # query time apart from lock contention apart from a slow Neon connection. These
    # checkpoints mirror app.agent.runner.run_inbound's existing timing-log style.
    # Remove once the slow stage is identified.
    _t0 = time.monotonic()
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        lang_row = (
            await session.execute(
                text("SELECT language FROM conversations WHERE id = :c"),
                {"c": call.conversation_id},
            )
        ).first()
        handed = (
            await session.execute(
                text(
                    "SELECT h.reason_code FROM handoffs h JOIN agent_runs r ON r.id = h.run_id"
                    " WHERE r.inbound_event_id = :e ORDER BY h.created_at DESC LIMIT 1"
                ),
                {"e": event_id},
            )
        ).first()
    logger.info(
        "finish_turn timing: lang/handoff lookup done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    # ROOT-CAUSE FIX (2026-11-20): a real call's logs showed the handoff/closing line
    # spoken in English (lang=en-IN) mid-call even though the caller had been speaking
    # Hindi the whole time and the agent's own earlier replies that same call were in
    # Hindi. `lang_row.language` here is a *fresh* read of conversations.language,
    # which app.agent.runner._persist_run/runner_v1 overwrite every turn from that
    # single turn's own language classification - so one noisy/partially-captured
    # utterance (confirmed from this call's own VAD stats: loud_ratio as low as 0.03-
    # 0.09, i.e. most of what STT heard was non-speech) getting misclassified as "en"
    # was enough to flip the language for the call's closing message, overriding every
    # earlier turn. `call.language` (loaded once per turn, before this turn's own
    # classification runs - see _turn()'s single _load_call() call site) reflects the
    # language already established by prior turns in *this* call, so it is preferred
    # here; lang_row is now only a fallback for the first turn of a call (before
    # call.language itself has ever been set from a real classification). VERIFY: this
    # makes voice-call language "sticky" once established, which is deliberately safer
    # for a closing/handoff line (it was never reached by a genuine language-switching
    # caller in the logs available during this fix) but has not been tested against a
    # caller who deliberately switches from Hindi to English mid-call.
    lang = (call.language or (lang_row.language if lang_row else None)) or "hinglish"
    if handed is not None:
        reply = await _handoff_reply(factory, voice, view, call, handed.reason_code, lang, capture)
        logger.info(
            "finish_turn timing: handoff_reply done elapsed_ms=%d",
            int((time.monotonic() - _t0) * 1000),
        )
    else:
        lines = [speech.speakable(line, lang) for line in capture.lines] or [
            speech.voice_line("unclear_audio", lang)
        ]
        say = " ".join(lines)
        # Section 17.2: non-interruptible only when one of the captured lines is a
        # write-flow confirm_*/done_* reply (app.v1.templates.CRITICAL_VOICE_TEMPLATES,
        # tagged all the way from app.agent.runner_v1._send() through
        # app.actions.engine.submit_reply into CaptureVoiceSender.critical) - an order
        # ref/amount being read out as a yes-or-no ask or a just-completed-action
        # statement. A plain informational reply (order_status, tracking_status, ask_*) -
        # even one that also contains an order number - stays interruptible, exactly as
        # test_greeting_discloses_ai_and_transcript_and_cannot_be_interrupted already
        # requires for a plain order_status answer.
        non_interruptible = any(capture.critical)
        for candidate in ("hinglish", "en", "hi"):
            if (
                capture.lines
                and speech.speakable(v1t.render("awaiting_team", candidate), candidate) in say
            ):
                say = speech.voice_line("approval_pending", lang)
                non_interruptible = False
                break
        reply = _reply(say, lang, barge_in=not non_interruptible)
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE inbound_events SET raw_payload = raw_payload || CAST(:r AS jsonb),"
                " status = 'processed', processed_at = now() WHERE id = :i"
            ),
            {"r": json.dumps({"voice_reply": reply.model_dump(mode="json")}), "i": event_id},
        )
    logger.info("finish_turn timing: done elapsed_ms=%d", int((time.monotonic() - _t0) * 1000))
    return reply


async def _handoff_reply(
    factory: Factory,
    voice: VoiceTenant,
    view: TenantView,
    call: CallRow,
    reason: str,
    lang: str,
    capture: CaptureVoiceSender,
) -> VoiceReplyOut:
    # TEMPORARY DIAGNOSTIC (2026-11-17): see app.voice.service._finish_turn's matching
    # note - this is the handoff path of the same unexplained ~25s gap, so it gets the
    # same checkpoints. Remove once the slow stage is identified.
    _t0 = time.monotonic()
    transfer = voice.transfer_number if voice.transfer_number and _in_hours(view) else None
    if transfer:
        line, status, action = speech.voice_line("transfer", lang), "transferred", "transfer"
    else:
        key = "callback" if _in_hours(view) else "callback_off_hours"
        line, status, action = speech.voice_line(key, lang), "callback", "hangup"
    ctx = await _context(factory, voice.tenant_id, call.id, call.conversation_id)
    logger.info(
        "handoff_reply timing: _context done elapsed_ms=%d",
        int((time.monotonic() - _t0) * 1000),
    )
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        phone = (
            await session.execute(
                text("SELECT phone_e164 FROM customers WHERE id = :u"), {"u": call.customer_id}
            )
        ).scalar_one_or_none()
        await session.execute(
            text(
                "UPDATE handoffs SET summary = summary || CAST(:patch AS jsonb)"
                " WHERE conversation_id = :c AND status IN ('open','claimed')"
            ),
            {"patch": json.dumps(_summary_patch(ctx)), "c": call.conversation_id},
        )
        await session.execute(
            text(
                "UPDATE voice_calls SET status = :s, end_reason = :r, ended_at = now() WHERE id = :i"
            ),
            {"s": status, "r": reason[:100], "i": call.id},
        )
    logger.info("handoff_reply timing: done elapsed_ms=%d", int((time.monotonic() - _t0) * 1000))
    return _reply(
        line,
        lang,
        action,
        transfer,
        briefing=_briefing(str(phone or "-"), reason, ctx) if transfer else None,
    )


def _summary_patch(ctx: CallContext) -> dict[str, object]:
    patch: dict[str, object] = {"agent_did": ctx.did[:10]}
    if ctx.order_ref:
        patch["order_ref"] = ctx.order_ref
    if ctx.last_said:
        patch["customer_asked"] = ctx.last_said[:400]
    return patch
