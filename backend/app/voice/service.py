import json
import logging
import time
import uuid
from dataclasses import dataclass, replace

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine
from app.actions.senders import Senders, build_senders
from app.agent import keywords, runner
from app.channels import identity
from app.channels.gupshup_parse import hash_token, tokens_equal
from app.contracts.contracts import NormalizedInbound
from app.core import timeutil
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import new_error_id
from app.core.flags import is_globally_paused
from app.core.mail import get_mailer
from app.core.masking import redact_for_llm
from app.handoffs import service as handoffs
from app.jobs import handlers, queue
from app.privacy import service as privacy
from app.v1 import templates as v1t
from app.v1.confirm import parse_confirmation
from app.v1.contracts import ConvUpdate, DecisionV1, HandoffSummaryV1
from app.v1.policy import PolicyV2, parse_any
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
    policy_row = (
        await session.execute(text("SELECT content FROM policies WHERE status = 'published'"))
    ).first()
    zone = (
        await session.execute(text("SELECT timezone FROM tenant_settings"))
    ).scalar_one_or_none()
    parsed = parse_any(policy_row.content) if policy_row else None
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
    hours = view.policy.office_hours
    return timeutil.is_within_hours(
        timeutil.utc_now(), hours.start, hours.end, hours.days, view.timezone
    )


@dataclass(frozen=True)
class CallContext:
    order_ref: str | None
    first_said: str | None
    last_said: str | None
    turns: int
    did: list[str]


async def _context(
    factory: Factory, tenant_id: uuid.UUID, call_id: uuid.UUID, conversation_id: uuid.UUID
) -> CallContext:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        call = (
            await session.execute(
                text("SELECT started_at, turn_count FROM voice_calls WHERE id = :i"), {"i": call_id}
            )
        ).one()
        order_ref = (
            await session.execute(
                text("SELECT active_order_ref FROM conversations WHERE id = :c"),
                {"c": conversation_id},
            )
        ).scalar_one_or_none()
        said = (
            (
                await session.execute(
                    text(
                        "SELECT content FROM messages WHERE conversation_id = :c AND channel = 'voice'"
                        " AND direction = 'in' AND created_at >= :s ORDER BY created_at"
                    ),
                    {"c": conversation_id, "s": call.started_at},
                )
            )
            .scalars()
            .all()
        )
        acts = (
            await session.execute(
                text(
                    "SELECT tool_name, status FROM agent_actions WHERE conversation_id = :c"
                    " AND tool_name IS NOT NULL AND created_at >= :s ORDER BY created_at"
                ),
                {"c": conversation_id, "s": call.started_at},
            )
        ).all()
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
    "CONFUSED": "Call par awaaz baar baar saaf nahi aayi.",
    "STEP_LIMIT": "Call bahut lambi chali, insaan ko sambhalni chahiye.",
    "POLICY_EXCEPTION": "Voice agent is brand ke liye abhi chalu nahi hai ya request policy ke bahar thi.",
    "ACTION_FAILED": "Call ke dauran takneeki dikkat aayi.",
    # Section 17.1 consent-capture (see _consent_turn):
    "HUMAN_REQUESTED": "Customer ne call shuru me hi insaan se baat karne ko kaha.",
    "CONSENT_DECLINED": "Customer ne AI assistant se baat karne / call record hone ke liye consent nahi diya.",
    # Section 17.4:
    "SILENCE_NO_RESPONSE": "Customer ne dobara prompt karne ke baad bhi jawaab nahi diya.",
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
) -> None:
    capture = CaptureVoiceSender()
    senders = await _senders(factory, voice.tenant_id, capture)
    await handoffs.create_handoff(
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
    await _open_handoff(
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
        key = "callback" if _in_hours(view) else "callback_off_hours"
        line = spoken_override or speech.voice_line(key, lang)
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

    Returns a VoiceReplyOut to end the turn here (human-request/explicit-no/literal-yes),
    or None to mean "treat this same utterance as the first real turn" - a customer who
    skips the question and states their need directly ("order 1001 cancel kar do") is not
    an explicit decline, so it is handled as implicit consent rather than trapped in a
    reprompt loop; _turn()'s existing low-confidence/step-limit/eligibility checks already
    cover a genuinely unclear or garbled utterance on this turn exactly as they do on every
    other turn, so no separate "unclear" handling is needed here."""
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
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE voice_calls SET consent_response = 'yes', consent_responded_at = now()"
                " WHERE id = :i"
            ),
            {"i": call.id},
        )
    if answer == "yes":
        async with session_scope(factory, tenant_id=voice.tenant_id) as session:
            await _bump(session, call, turns=1)
        return _reply(speech.voice_line("consent_ack", lang), lang)
    call.consent_response = "yes"
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
        reply = await _consent_turn(factory, voice, view, call, event, utterance, lang)
        if reply is not None:
            return reply
        # Implicit consent granted (customer went straight into a request instead of a
        # literal yes/no) - fall through and process this same utterance as the first
        # real turn, instead of discarding it.

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
    )
    try:
        await runner.run_inbound(
            ctx, voice.tenant_id, event_id, call.conversation_id, message_id, inbound
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
    return await _finish_turn(factory, voice, view, call, event, event_id, utterance, capture)


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
    lang = (lang_row.language if lang_row and lang_row.language else call.language) or "hinglish"
    if handed is not None:
        reply = await _handoff_reply(factory, voice, view, call, handed.reason_code, lang, capture)
    else:
        lines = [speech.speakable(line, lang) for line in capture.lines] or [
            speech.voice_line("unclear_audio", lang)
        ]
        say = " ".join(lines)
        for candidate in ("hinglish", "en", "hi"):
            if (
                capture.lines
                and speech.speakable(v1t.render("awaiting_team", candidate), candidate) in say
            ):
                say = speech.voice_line("approval_pending", lang)
                break
        # Section 17.2: an order ref or amount being read out (confirm_*/done_* templates,
        # tracking status) must not be cut off mid-number by a false barge-in trigger.
        reply = _reply(say, lang, barge_in=not speech.has_spoken_number(say))
    async with session_scope(factory, tenant_id=voice.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE inbound_events SET raw_payload = raw_payload || CAST(:r AS jsonb),"
                " status = 'processed', processed_at = now() WHERE id = :i"
            ),
            {"r": json.dumps({"voice_reply": reply.model_dump(mode="json")}), "i": event_id},
        )
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
    transfer = voice.transfer_number if voice.transfer_number and _in_hours(view) else None
    if transfer:
        line, status, action = speech.voice_line("transfer", lang), "transferred", "transfer"
    else:
        key = "callback" if _in_hours(view) else "callback_off_hours"
        line, status, action = speech.voice_line(key, lang), "callback", "hangup"
    ctx = await _context(factory, voice.tenant_id, call.id, call.conversation_id)
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
