import secrets
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import Principal
from app.channels.gupshup_parse import hash_token
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.errors import AppError
from app.voice.contracts import (
    PublicNumberConfirmIn,
    PublicNumberIn,
    VoiceCallDetail,
    VoiceCallOut,
    VoiceFeedbackIn,
    VoiceSettingsIn,
    VoiceSettingsOut,
    VoiceStatsOut,
    VoiceTurnOut,
)
from app.voice.providers import provider_names


def _forwarding_codes(called_number: str | None) -> tuple[str | None, str | None]:
    """GSM call-forwarding codes: unconditional forward to the AI number, and to switch it off.

    These are telecom-standard USSD codes (not carrier/app-specific) that work the same way
    on Jio/Airtel/Vi/BSNL in India.
    """
    if not called_number:
        return None, None
    digits = called_number.lstrip("+")
    return f"*21*{digits}#", "#21#"


async def get_settings_out(session: AsyncSession, tenant_id: uuid.UUID) -> VoiceSettingsOut:
    row = (
        await session.execute(
            text(
                "SELECT called_number, transfer_number, enabled, provider, telephony_mode,"
                " client_public_number, carrier, forwarding_confirmed"
                " FROM voice_numbers WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    if row is None:
        return VoiceSettingsOut(configured=False, providers=provider_names())
    forwarding_code, forwarding_off_code = _forwarding_codes(row.called_number)
    return VoiceSettingsOut(
        configured=True,
        called_number=row.called_number,
        transfer_number=row.transfer_number,
        enabled=bool(row.enabled),
        provider=row.provider,
        telephony_mode=row.telephony_mode,
        providers=provider_names(),
        client_public_number=row.client_public_number,
        carrier=row.carrier,
        forwarding_confirmed=bool(row.forwarding_confirmed),
        forwarding_code=forwarding_code,
        forwarding_off_code=forwarding_off_code,
    )


async def put_settings(
    session: AsyncSession, tenant_id: uuid.UUID, principal: Principal, body: VoiceSettingsIn
) -> VoiceSettingsOut:
    owner = (
        await session.execute(
            text("SELECT tenant_id FROM voice_numbers WHERE called_number = :n"),
            {"n": body.called_number},
        )
    ).first()
    if owner is not None and owner.tenant_id != tenant_id:
        raise AppError("E-CONF-001", detail="called_number in use")
    mine = (
        await session.execute(
            text(
                "SELECT called_number, client_public_number, carrier, forwarding_confirmed"
                " FROM voice_numbers WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    webhook_url: str | None = None
    if mine is None or mine.called_number != body.called_number or body.rotate_webhook:
        token = secrets.token_urlsafe(32)
        # The AI (called_number) rotation is admin-only and unrelated to the client's own
        # business number / forwarding setup — carry those fields over, don't wipe them.
        keep_public_number = mine.client_public_number if mine is not None else None
        keep_carrier = mine.carrier if mine is not None else None
        keep_confirmed = bool(mine.forwarding_confirmed) if mine is not None else False
        await session.execute(
            text("DELETE FROM voice_numbers WHERE tenant_id = :t"), {"t": tenant_id}
        )
        await session.execute(
            text(
                "INSERT INTO voice_numbers (called_number, webhook_token_hash, tenant_id, enabled,"
                " transfer_number, provider, telephony_mode, client_public_number, carrier,"
                " forwarding_confirmed)"
                " VALUES (:n, :h, :tid, :e, :t, :p, :m, :cpn, :car, :fc)"
            ),
            {
                "n": body.called_number,
                "h": hash_token(token),
                "tid": tenant_id,
                "e": body.enabled,
                "t": body.transfer_number,
                "p": body.provider,
                "m": body.telephony_mode,
                "cpn": keep_public_number,
                "car": keep_carrier,
                "fc": keep_confirmed,
            },
        )
        webhook_url = f"{get_settings().public_base_url.rstrip('/')}/webhooks/voice/{token}"
    else:
        await session.execute(
            text(
                "UPDATE voice_numbers SET enabled = :e, transfer_number = :t, provider = :p,"
                " telephony_mode = :m, updated_at = now()"
                " WHERE tenant_id = :i"
            ),
            {
                "e": body.enabled,
                "t": body.transfer_number,
                "p": body.provider,
                "m": body.telephony_mode,
                "i": tenant_id,
            },
        )
    await record(
        session,
        AuditEvent(
            action="voice.settings_changed",
            entity_type="voice_numbers",
            entity_id=str(tenant_id),
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )
    out = await get_settings_out(session, tenant_id)
    return out.model_copy(update={"webhook_url": webhook_url})


def _call_out(row: Any) -> VoiceCallOut:
    return VoiceCallOut(
        id=str(row.id),
        status=row.status,
        caller_masked=row.caller_masked,
        turn_count=row.turn_count,
        end_reason=row.end_reason,
        started_at=row.started_at.isoformat(),
        ended_at=row.ended_at.isoformat() if row.ended_at else None,
        conversation_id=str(row.conversation_id),
        order_ref=row.order_ref,
        avg_latency_ms=int(row.latency_total_ms // row.turn_count) if row.turn_count else 0,
        max_latency_ms=row.latency_max_ms,
        slow_turns=row.slow_turns,
        feedback=row.feedback,
        feedback_note=row.feedback_note,
        summary_status=row.summary_status,
    )


_SELECT = (
    "SELECT id, status, caller_masked, turn_count, end_reason, started_at, ended_at,"
    " conversation_id, order_ref, latency_total_ms, latency_max_ms, slow_turns, feedback,"
    " feedback_note, summary_status FROM voice_calls"
)


async def list_calls(session: AsyncSession, limit: int) -> list[VoiceCallOut]:
    rows = (
        await session.execute(text(_SELECT + " ORDER BY started_at DESC LIMIT :l"), {"l": limit})
    ).all()
    return [_call_out(r) for r in rows]


async def call_detail(session: AsyncSession, call_id: uuid.UUID) -> VoiceCallDetail:
    row = (await session.execute(text(_SELECT + " WHERE id = :i"), {"i": call_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    turns: list[VoiceTurnOut] = []
    spoken = (
        await session.execute(
            text(
                "SELECT created_at, content FROM messages WHERE conversation_id = :c"
                " AND direction = 'out' AND action_id IS NULL AND created_at >= :s"
                " AND channel = 'voice'"
            ),
            {"c": row.conversation_id, "s": row.started_at},
        )
    ).all()
    heard = (
        await session.execute(
            text(
                "SELECT m.created_at, m.content, e.raw_payload->'voice_reply'->>'say' AS say,"
                " COALESCE(e.processed_at, e.received_at) AS replied_at FROM messages m"
                " JOIN inbound_events e ON e.id = m.inbound_event_id"
                " WHERE m.conversation_id = :c AND m.channel = 'voice'"
                " AND m.direction = 'in' AND m.created_at >= :s"
            ),
            {"c": row.conversation_id, "s": row.started_at},
        )
    ).all()
    timeline: list[tuple[Any, str, str]] = []
    for s in spoken:
        timeline.append((s.created_at, "agent", s.content))
    for h in heard:
        timeline.append((h.created_at, "caller", h.content))
        if h.say:
            timeline.append((h.replied_at, "agent", h.say))
    for at, who, content in sorted(timeline, key=lambda item: (item[0], item[1] != "caller")):
        turns.append(VoiceTurnOut(at=at.isoformat(), speaker=who, text=content))
    return VoiceCallDetail(**_call_out(row).model_dump(), turns=turns)


async def set_feedback(
    session: AsyncSession, principal: Principal, call_id: uuid.UUID, body: VoiceFeedbackIn
) -> VoiceCallOut:
    updated = (
        await session.execute(
            text(
                "UPDATE voice_calls SET feedback = :v, feedback_note = :n, feedback_at = now()"
                " WHERE id = :i RETURNING id"
            ),
            {"v": body.verdict, "n": body.note, "i": call_id},
        )
    ).first()
    if updated is None:
        raise AppError("E-NF-001")
    await record(
        session,
        AuditEvent(
            action="voice.feedback",
            entity_type="voice_calls",
            entity_id=str(call_id),
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )
    row = (await session.execute(text(_SELECT + " WHERE id = :i"), {"i": call_id})).one()
    return _call_out(row)


async def stats(session: AsyncSession, days: int) -> VoiceStatsOut:
    row = (
        await session.execute(
            text(
                "SELECT count(*) AS calls,"
                " count(*) FILTER (WHERE status = 'completed') AS handled,"
                " count(*) FILTER (WHERE status = 'transferred') AS transferred,"
                " count(*) FILTER (WHERE status = 'callback') AS callback,"
                " count(*) FILTER (WHERE status = 'failed') AS failed,"
                " COALESCE(avg(turn_count), 0) AS avg_turns,"
                " COALESCE(sum(latency_total_ms) / NULLIF(sum(turn_count), 0), 0) AS avg_latency,"
                " COALESCE(sum(slow_turns), 0) AS slow,"
                " count(*) FILTER (WHERE feedback = 'good') AS good,"
                " count(*) FILTER (WHERE feedback = 'bad') AS bad"
                " FROM voice_calls WHERE started_at >= now() - make_interval(days => :d)"
            ),
            {"d": days},
        )
    ).one()
    calls = int(row.calls)
    return VoiceStatsOut(
        days=days,
        calls=calls,
        handled_by_agent=int(row.handled),
        transferred=int(row.transferred),
        callback=int(row.callback),
        failed=int(row.failed),
        avg_turns=round(float(row.avg_turns), 1),
        avg_latency_ms=int(row.avg_latency),
        slow_turns=int(row.slow),
        feedback_good=int(row.good),
        feedback_bad=int(row.bad),
        agent_rate_percent=int(round(100 * int(row.handled) / calls)) if calls else 0,
    )


async def put_public_number(
    session: AsyncSession, tenant_id: uuid.UUID, principal: Principal, body: PublicNumberIn
) -> VoiceSettingsOut:
    """Owner sets/edits their own public business number + carrier (display/reference only).

    Never touches `called_number` (the AI's real receiving number, admin-only). No provider
    API call here — the client activates forwarding themselves from their own phone.
    """
    existing = (
        await session.execute(
            text("SELECT called_number FROM voice_numbers WHERE tenant_id = :t"), {"t": tenant_id}
        )
    ).first()
    if existing is None:
        raise AppError("E-NF-001")
    await session.execute(
        text(
            "UPDATE voice_numbers SET client_public_number = :n, carrier = :c,"
            " forwarding_confirmed = false, updated_at = now() WHERE tenant_id = :t"
        ),
        {"n": body.client_public_number, "c": body.carrier, "t": tenant_id},
    )
    await record(
        session,
        AuditEvent(
            action="voice.public_number_changed",
            entity_type="voice_numbers",
            entity_id=str(tenant_id),
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )
    return await get_settings_out(session, tenant_id)


async def confirm_forwarding(
    session: AsyncSession, tenant_id: uuid.UUID, principal: Principal, body: PublicNumberConfirmIn
) -> VoiceSettingsOut:
    """Owner confirms (or un-confirms) that they've dialled the forwarding code on their phone."""
    row = (
        await session.execute(
            text("SELECT client_public_number FROM voice_numbers WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).first()
    if row is None:
        raise AppError("E-NF-001")
    if body.forwarding_confirmed and not row.client_public_number:
        raise AppError("E-VAL-001")
    await session.execute(
        text(
            "UPDATE voice_numbers SET forwarding_confirmed = :c, updated_at = now()"
            " WHERE tenant_id = :t"
        ),
        {"c": body.forwarding_confirmed, "t": tenant_id},
    )
    await record(
        session,
        AuditEvent(
            action="voice.forwarding_confirmed",
            entity_type="voice_numbers",
            entity_id=str(tenant_id),
            new_value={"forwarding_confirmed": body.forwarding_confirmed},
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )
    return await get_settings_out(session, tenant_id)
