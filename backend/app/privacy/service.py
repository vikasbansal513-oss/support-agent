import re
import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.contracts import PrivacyRequestOut
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError
from app.privacy.notices import NOTICE_VERSION

Factory = async_sessionmaker[AsyncSession]


async def _delete(session: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    result = cast("CursorResult[Any]", await session.execute(text(sql), params))
    return int(result.rowcount)


_DIGITS = re.compile(r"\D")


def normalize_subject(ref: str) -> tuple[str, str]:
    value = ref.strip()
    if "@" in value:
        return "email", value.lower()
    digits = _DIGITS.sub("", value)
    if len(digits) == 10:
        digits = "91" + digits
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        raise AppError("E-VAL-001", detail="subject_ref must be a phone number or email")
    return "phone", "+" + digits


async def find_customer(session: AsyncSession, ref: str) -> uuid.UUID | None:
    kind, value = normalize_subject(ref)
    column = "email_lower" if kind == "email" else "phone_e164"
    row = (
        await session.execute(
            text(f"SELECT id FROM customers WHERE {column} = :v"),  # noqa: S608
            {"v": value},
        )
    ).first()
    return None if row is None else uuid.UUID(str(row.id))


_REQUEST_COLS = "id, kind, status, due_at, created_at, correction_field, correction_value"


def _out(row: Any) -> PrivacyRequestOut:
    return PrivacyRequestOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        due_at=row.due_at,
        created_at=row.created_at,
        correction_field=row.correction_field,
        correction_value=row.correction_value,
    )


async def list_requests(factory: Factory, tenant_id: uuid.UUID) -> list[PrivacyRequestOut]:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT {_REQUEST_COLS} FROM privacy_requests"  # noqa: S608
                    " WHERE tenant_id = :t ORDER BY created_at DESC, id DESC LIMIT 200"
                ),
                {"t": tenant_id},
            )
        ).all()
    return [_out(r) for r in rows]


async def create_request(
    factory: Factory,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    subject_ref: str,
    kind: str,
    correction_field: str | None = None,
    correction_value: str | None = None,
) -> PrivacyRequestOut:
    _, normalized = normalize_subject(subject_ref)
    try:
        async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO privacy_requests (tenant_id, subject_ref, kind, created_by,"  # noqa: S608
                        " correction_field, correction_value)"
                        f" VALUES (:t, :s, :k, :u, :cf, :cv) RETURNING {_REQUEST_COLS}"
                    ),
                    {
                        "t": tenant_id,
                        "s": normalized,
                        "k": kind,
                        "u": user_id,
                        "cf": correction_field,
                        "cv": correction_value,
                    },
                )
            ).one()
            await record(
                session,
                AuditEvent(
                    action="privacy.request_created",
                    entity_type="privacy_request",
                    entity_id=str(row.id),
                    new_value={
                        "kind": kind,
                        "correction_field": correction_field,
                        "correction_value": correction_value,
                    },
                    actor_user_id=user_id,
                    actor_role="owner",
                ),
            )
    except IntegrityError as exc:
        raise AppError("E-PRV-001") from exc
    return _out(row)


async def decide_correction(
    factory: Factory,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    approve: bool,
    reason: str | None = None,
) -> PrivacyRequestOut:
    """Owner review step for a kind=correct privacy request.

    Approving applies the customer's requested phone/email correction to their own
    `customers` row (never another tenant's - RLS scopes every statement below to
    tenant_id) and marks the request done; rejecting just closes it with a reason.
    This never touches `refund_create`/order data - it only edits contact details,
    kept entirely separate from the export/delete flows above.
    """
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        row = (
            await session.execute(
                text(
                    f"SELECT {_REQUEST_COLS}, subject_ref FROM privacy_requests"  # noqa: S608
                    " WHERE id = :i FOR UPDATE"
                ),
                {"i": request_id},
            )
        ).first()
        if row is None:
            raise AppError("E-NF-001")
        if row.kind != "correct" or row.status != "open":
            raise AppError("E-PRV-002")
        if approve:
            if row.correction_field is None or row.correction_value is None:
                raise AppError("E-PRV-002")
            kind, normalized_value = normalize_subject(row.correction_value)
            if kind != row.correction_field:
                raise AppError("E-PRV-004")
            customer_id = await find_customer(session, row.subject_ref)
            if customer_id is None:
                raise AppError("E-PRV-003")
            column = "email_lower" if row.correction_field == "email" else "phone_e164"
            await session.execute(
                text(f"UPDATE customers SET {column} = :v WHERE id = :c"),  # noqa: S608
                {"v": normalized_value, "c": customer_id},
            )
            await session.execute(
                text("UPDATE privacy_requests SET status = 'done', done_at = now() WHERE id = :i"),
                {"i": request_id},
            )
            await record(
                session,
                AuditEvent(
                    action="privacy.correction_approved",
                    entity_type="privacy_request",
                    entity_id=str(request_id),
                    new_value={"field": row.correction_field, "value": normalized_value},
                    reason=reason,
                    actor_user_id=user_id,
                    actor_role="owner",
                ),
            )
        else:
            await session.execute(
                text(
                    "UPDATE privacy_requests SET status = 'rejected', done_at = now() WHERE id = :i"
                ),
                {"i": request_id},
            )
            await record(
                session,
                AuditEvent(
                    action="privacy.correction_rejected",
                    entity_type="privacy_request",
                    entity_id=str(request_id),
                    reason=reason,
                    actor_user_id=user_id,
                    actor_role="owner",
                ),
            )
        updated = (
            await session.execute(
                text(f"SELECT {_REQUEST_COLS} FROM privacy_requests WHERE id = :i"),  # noqa: S608
                {"i": request_id},
            )
        ).one()
    return _out(updated)


async def purge_conversations(session: AsyncSession, conversation_ids: list[uuid.UUID]) -> int:
    if not conversation_ids:
        return 0
    params = {"ids": conversation_ids}
    steps = (
        "DELETE FROM conversation_state WHERE conversation_id = ANY(:ids)",
        "DELETE FROM shadow_verdicts WHERE action_id IN"
        " (SELECT id FROM agent_actions WHERE conversation_id = ANY(:ids))",
        "DELETE FROM answer_reports WHERE message_id IN"
        " (SELECT id FROM messages WHERE conversation_id = ANY(:ids))",
        "DELETE FROM llm_calls WHERE run_id IN"
        " (SELECT id FROM agent_runs WHERE conversation_id = ANY(:ids))",
        "DELETE FROM handoffs WHERE conversation_id = ANY(:ids)",
        "DELETE FROM agent_actions WHERE conversation_id = ANY(:ids)",
        "DELETE FROM messages WHERE conversation_id = ANY(:ids)",
    )
    for sql in steps:
        await session.execute(text(sql), params)
    events = (
        (
            await session.execute(
                text("SELECT inbound_event_id FROM agent_runs WHERE conversation_id = ANY(:ids)"),
                params,
            )
        )
        .scalars()
        .all()
    )
    await session.execute(text("DELETE FROM agent_runs WHERE conversation_id = ANY(:ids)"), params)
    if events:
        await session.execute(
            text("DELETE FROM inbound_events WHERE id = ANY(:e)"), {"e": list(events)}
        )
    await session.execute(text("DELETE FROM conversations WHERE id = ANY(:ids)"), params)
    return len(conversation_ids)


async def erase_customer(session: AsyncSession, customer_id: uuid.UUID) -> int:
    customer = (
        await session.execute(
            text("SELECT phone_e164, email_lower FROM customers WHERE id = :c"), {"c": customer_id}
        )
    ).one()
    conversations = list(
        (
            await session.execute(
                text("SELECT id FROM conversations WHERE customer_id = :c"), {"c": customer_id}
            )
        )
        .scalars()
        .all()
    )
    purged = await purge_conversations(session, conversations)
    await session.execute(
        text(
            "DELETE FROM inbound_events WHERE (raw_payload #>> '{normalized,from_phone}' = :p"
            " OR raw_payload #>> '{normalized,from_email}' = :e)"
        ),
        {"p": customer.phone_e164, "e": customer.email_lower},
    )
    await session.execute(text("DELETE FROM opt_outs WHERE customer_id = :c"), {"c": customer_id})
    await session.execute(
        text("DELETE FROM action_usage WHERE customer_id = :c"), {"c": customer_id}
    )
    await session.execute(text("DELETE FROM customers WHERE id = :c"), {"c": customer_id})
    return purged


async def export_customer(
    factory: Factory, tenant_id: uuid.UUID, user_id: uuid.UUID, subject_ref: str
) -> dict[str, Any]:
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        customer_id = await find_customer(session, subject_ref)
        _, normalized = normalize_subject(subject_ref)
        data: dict[str, Any] = {
            "subject": normalized,
            "notice_version": NOTICE_VERSION,
            "generated_at": timeutil.utc_now().isoformat(),
            "conversations": [],
            "consents": [],
            "opt_outs": [],
        }
        if customer_id is not None:
            convs = (
                await session.execute(
                    text(
                        "SELECT id, channel, status, created_at FROM conversations"
                        " WHERE customer_id = :c ORDER BY created_at"
                    ),
                    {"c": customer_id},
                )
            ).all()
            for conv in convs:
                msgs = (
                    await session.execute(
                        text(
                            "SELECT direction, sender, content, created_at FROM messages"
                            " WHERE conversation_id = :c ORDER BY created_at, id"
                        ),
                        {"c": conv.id},
                    )
                ).all()
                data["conversations"].append(
                    {
                        "channel": conv.channel,
                        "status": conv.status,
                        "created_at": conv.created_at.isoformat(),
                        "messages": [
                            {
                                "direction": m.direction,
                                "sender": m.sender,
                                "content": m.content,
                                "at": m.created_at.isoformat(),
                            }
                            for m in msgs
                        ],
                    }
                )
            data["opt_outs"] = [
                {"channel": r.channel, "source": r.source}
                for r in (
                    await session.execute(
                        text("SELECT channel, source FROM opt_outs WHERE customer_id = :c"),
                        {"c": customer_id},
                    )
                ).all()
            ]
            data["consents"] = [
                {"purpose": r.purpose, "granted": r.granted, "at": r.created_at.isoformat()}
                for r in (
                    await session.execute(
                        text(
                            "SELECT purpose, granted, created_at FROM consents"
                            " WHERE subject_type = 'customer' AND subject_id = :c ORDER BY created_at"
                        ),
                        {"c": customer_id},
                    )
                ).all()
            ]
        await session.execute(
            text(
                "UPDATE privacy_requests SET status = 'done', done_at = now()"
                " WHERE subject_ref = :s AND kind = 'export' AND status = 'open'"
            ),
            {"s": normalized},
        )
        await record(
            session,
            AuditEvent(
                action="privacy.exported",
                entity_type="customer",
                entity_id=str(customer_id) if customer_id else None,
                actor_user_id=user_id,
                actor_role="owner",
            ),
        )
    return data


async def execute_delete_requests(factory: Factory, tenant_id: uuid.UUID) -> int:
    done = 0
    async with session_scope(factory, tenant_id=tenant_id) as session:
        hold = (
            await session.execute(
                text("SELECT legal_hold FROM tenants WHERE id = :t"), {"t": tenant_id}
            )
        ).scalar_one()
        if hold:
            return 0
        requests = (
            await session.execute(
                text(
                    "SELECT id, subject_ref FROM privacy_requests"
                    " WHERE kind = 'delete' AND status = 'open' ORDER BY created_at FOR UPDATE"
                )
            )
        ).all()
        for req in requests:
            customer_id = await find_customer(session, req.subject_ref)
            if customer_id is not None:
                await erase_customer(session, customer_id)
            await session.execute(
                text("UPDATE privacy_requests SET status = 'done', done_at = now() WHERE id = :i"),
                {"i": req.id},
            )
            await record(
                session,
                AuditEvent(
                    action="privacy.deleted",
                    entity_type="privacy_request",
                    entity_id=str(req.id),
                    actor_role="system",
                ),
            )
            done += 1
    return done


async def record_consent(
    session: AsyncSession,
    customer_id: uuid.UUID,
    purpose: str,
    granted: bool,
    source: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO consents (subject_type, subject_id, purpose, notice_version, granted, source)"
            " VALUES ('customer', :s, :p, :v, :g, :src)"
        ),
        {"s": customer_id, "p": purpose, "v": NOTICE_VERSION, "g": granted, "src": source},
    )


async def run_retention(
    factory: Factory, tenant_id: uuid.UUID, now: datetime | None = None
) -> dict[str, int]:
    settings = get_settings()
    moment = now or timeutil.utc_now()
    cutoff = moment - timedelta(days=settings.retention_days)
    technical = moment - timedelta(days=settings.technical_retention_days)
    result = {"conversations": 0, "external_calls": 0, "jobs": 0, "global_rows": 0}
    async with session_scope(factory, tenant_id=tenant_id) as session:
        hold = (
            await session.execute(
                text("SELECT legal_hold FROM tenants WHERE id = :t"), {"t": tenant_id}
            )
        ).scalar_one()
        if not hold:
            ids = list(
                (
                    await session.execute(
                        text(
                            "SELECT id FROM conversations WHERE tenant_id = :t"
                            " AND COALESCE(last_message_at, created_at) < :c"
                            " AND id NOT IN (SELECT conversation_id FROM handoffs"
                            "  WHERE status IN ('open','claimed')) LIMIT 500"
                        ),
                        {"t": tenant_id, "c": cutoff},
                    )
                )
                .scalars()
                .all()
            )
            result["conversations"] = await purge_conversations(session, ids)
            await session.execute(
                text(
                    "DELETE FROM inbound_events WHERE tenant_id = :t AND received_at < :c"
                    " AND id NOT IN (SELECT inbound_event_id FROM messages"
                    "  WHERE inbound_event_id IS NOT NULL)"
                    " AND id NOT IN (SELECT inbound_event_id FROM agent_runs)"
                ),
                {"t": tenant_id, "c": cutoff},
            )
            if result["conversations"]:
                await record(
                    session,
                    AuditEvent(
                        action="retention.conversations_deleted",
                        new_value={
                            "count": result["conversations"],
                            "days": settings.retention_days,
                        },
                        actor_role="system",
                    ),
                )
        result["external_calls"] = await _delete(
            session,
            "DELETE FROM external_calls WHERE tenant_id = :t AND created_at < :c",
            {"t": tenant_id, "c": technical},
        )
        result["jobs"] = await _delete(
            session,
            "DELETE FROM jobs WHERE tenant_id = :t AND status = 'done' AND finished_at < :c",
            {"t": tenant_id, "c": technical},
        )
    async with session_scope(factory, super_admin=True) as session:
        for sql in (
            "DELETE FROM rate_limits WHERE window_start < :c",
            "DELETE FROM email_tokens WHERE expires_at < :c",
            "DELETE FROM refresh_tokens WHERE expires_at < :c",
        ):
            result["global_rows"] += await _delete(session, sql, {"c": technical})
    return result


CONSENT_PURPOSE = {"whatsapp": "whatsapp_optin", "email": "email_contact", "voice": "voice_contact"}


async def ensure_consent(
    session: AsyncSession, customer_id: uuid.UUID, channel: str, source: str
) -> None:
    purpose = CONSENT_PURPOSE.get(channel, "email_contact")
    exists = (
        await session.execute(
            text(
                "SELECT 1 FROM consents WHERE subject_type = 'customer' AND subject_id = :c"
                " AND purpose = :p LIMIT 1"
            ),
            {"c": customer_id, "p": purpose},
        )
    ).first()
    if exists is None:
        await record_consent(session, customer_id, purpose, True, source)
        await record_consent(session, customer_id, "ai_processing", True, source)
