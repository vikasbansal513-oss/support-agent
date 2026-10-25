import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.pagination import decode_offset, encode_offset
from app.auth.deps import require_user
from app.auth.service import Principal
from app.contracts.contracts import HandoffOut, HandoffSummary, Page
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

router = APIRouter(prefix="/handoffs", tags=["handoffs"])
Factory = async_sessionmaker[AsyncSession]
_COLUMNS = (
    "id, conversation_id, reason_code, priority, status, would_handoff, summary, sla_due_at,"
    " created_at, assigned_user_id"
)


def _out(row: Any) -> HandoffOut:
    breached = (
        row.status != "resolved"
        and row.sla_due_at is not None
        and row.sla_due_at < timeutil.utc_now()
    )
    return HandoffOut(
        id=row.id,
        conversation_id=row.conversation_id,
        reason_code=row.reason_code,
        priority=row.priority,
        status=row.status,
        would_handoff=row.would_handoff,
        summary=HandoffSummary.model_validate(row.summary),
        sla_due_at=row.sla_due_at,
        sla_breached=breached,
        created_at=row.created_at,
    )


async def handoff_out(session: AsyncSession, handoff_id: uuid.UUID) -> HandoffOut:
    row = (
        await session.execute(
            text(f"SELECT {_COLUMNS} FROM handoffs WHERE id = :i"), {"i": handoff_id}
        )
    ).first()
    if row is None:
        raise AppError("E-NF-001")
    return _out(row)


@router.get("")
async def list_handoffs(
    status: Literal["open", "claimed", "resolved"] | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = None,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Page:
    offset = decode_offset(cursor)
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM handoffs WHERE would_handoff = false"
                    " AND ((CAST(:s AS text) IS NULL AND status IN ('open','claimed')) OR status = :s)"
                    " ORDER BY (priority = 'urgent') DESC, sla_due_at ASC NULLS LAST, created_at, id"
                    " LIMIT :l OFFSET :o"
                ),
                {"s": status, "l": limit + 1, "o": offset},
            )
        ).all()
    return Page(
        items=[_out(r) for r in rows[:limit]],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
    )


@router.post("/{handoff_id}/claim")
async def claim(
    handoff_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> HandoffOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, assigned_user_id, conversation_id, would_handoff FROM handoffs"
                    " WHERE id = :i FOR UPDATE"
                ),
                {"i": handoff_id},
            )
        ).first()
        if row is None or row.would_handoff:
            raise AppError("E-NF-001")
        if row.status == "resolved":
            raise AppError("E-HO-001")
        if row.status == "claimed" and row.assigned_user_id != principal.user_id:
            raise AppError("E-HO-001")
        await session.execute(
            text("UPDATE handoffs SET status = 'claimed', assigned_user_id = :u WHERE id = :i"),
            {"u": principal.user_id, "i": handoff_id},
        )
        await session.execute(
            text("UPDATE conversations SET human_active = true WHERE id = :c"),
            {"c": row.conversation_id},
        )
        await record(
            session,
            AuditEvent(
                action="handoff.claimed",
                entity_type="handoff",
                entity_id=str(handoff_id),
                actor_user_id=principal.user_id,
                actor_role=principal.role,
            ),
        )
        return await handoff_out(session, handoff_id)


@router.post("/{handoff_id}/resolve")
async def resolve(
    handoff_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> HandoffOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, assigned_user_id, conversation_id, would_handoff FROM handoffs"
                    " WHERE id = :i FOR UPDATE"
                ),
                {"i": handoff_id},
            )
        ).first()
        if row is None or row.would_handoff:
            raise AppError("E-NF-001")
        if row.status == "claimed" and row.assigned_user_id != principal.user_id:
            raise AppError("E-HO-001")
        if row.status == "resolved":
            return await handoff_out(session, handoff_id)
        await session.execute(
            text(
                "UPDATE handoffs SET status = 'resolved', resolved_at = now(), resolved_by = :u"
                " WHERE id = :i"
            ),
            {"u": principal.user_id, "i": handoff_id},
        )
        await session.execute(
            text(
                "UPDATE conversations SET human_active = false,"
                " status = CASE WHEN status = 'handoff' THEN 'open' ELSE status END WHERE id = :c"
            ),
            {"c": row.conversation_id},
        )
        await record(
            session,
            AuditEvent(
                action="handoff.resolved",
                entity_type="handoff",
                entity_id=str(handoff_id),
                actor_user_id=principal.user_id,
                actor_role=principal.role,
            ),
        )
        return await handoff_out(session, handoff_id)
