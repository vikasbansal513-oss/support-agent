import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine
from app.actions.senders import build_senders
from app.api.pagination import decode_offset, encode_offset
from app.auth.deps import require_user
from app.auth.service import Principal
from app.contracts.contracts import ActionOut, Page, VerdictIn
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.integrations import service as integrations

router = APIRouter(prefix="/actions", tags=["actions"])
Factory = async_sessionmaker[AsyncSession]
_SELECT = (
    "SELECT a.id, a.conversation_id, a.action_type, a.status, a.body_text, a.created_at,"
    " v.verdict, (SELECT m.content FROM messages m JOIN agent_runs r ON r.inbound_event_id = m.inbound_event_id"
    " WHERE r.id = a.run_id AND m.direction = 'in' LIMIT 1) AS customer_message"
    " FROM agent_actions a LEFT JOIN shadow_verdicts v ON v.action_id = a.id"
)


def _out(row: Any) -> ActionOut:
    return ActionOut(
        id=row.id,
        conversation_id=row.conversation_id,
        action_type=row.action_type,
        status=row.status,
        body_text=row.body_text,
        customer_message=row.customer_message,
        verdict=row.verdict,
        created_at=row.created_at,
    )


async def action_out(session: AsyncSession, action_id: uuid.UUID) -> ActionOut:
    row = (await session.execute(text(f"{_SELECT} WHERE a.id = :i"), {"i": action_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    return _out(row)


@router.get("")
async def list_actions(
    status: Literal["shadow", "draft"] | None = None,
    unreviewed: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
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
                    f"{_SELECT} WHERE a.action_type = 'reply_customer'"
                    " AND ((CAST(:s AS text) IS NULL AND a.status IN ('shadow','draft')) OR a.status = :s)"
                    " AND (NOT :u OR v.verdict IS NULL)"
                    " ORDER BY a.created_at DESC, a.id LIMIT :l OFFSET :o"
                ),
                {"s": status, "u": unreviewed, "l": limit + 1, "o": offset},
            )
        ).all()
    return Page(
        items=[_out(r) for r in rows[:limit]],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
    )


@router.post("/{action_id}/approve")
async def approve(
    action_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    transport: httpx.AsyncBaseTransport | None = Depends(integrations.get_transport),  # noqa: B008
) -> ActionOut:
    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise AppError("E-AUTH-007")
    senders = await build_senders(factory, tenant_id, transport)
    await engine.approve(factory, senders, tenant_id, action_id, principal.user_id)
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        return await action_out(session, action_id)


@router.post("/{action_id}/reject")
async def reject(
    action_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ActionOut:
    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise AppError("E-AUTH-007")
    await engine.reject(factory, tenant_id, action_id, principal.user_id)
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        return await action_out(session, action_id)


@router.post("/{action_id}/verdict")
async def verdict(
    action_id: uuid.UUID,
    body: VerdictIn,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ActionOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status FROM agent_actions WHERE id = :i AND action_type = 'reply_customer'"
                ),
                {"i": action_id},
            )
        ).first()
        if row is None:
            raise AppError("E-NF-001")
        await session.execute(
            text(
                "INSERT INTO shadow_verdicts (action_id, verdict, note, by_user)"
                " VALUES (:a, :v, :n, :u) ON CONFLICT (tenant_id, action_id)"
                " DO UPDATE SET verdict = EXCLUDED.verdict, note = EXCLUDED.note, by_user = EXCLUDED.by_user"
            ),
            {"a": action_id, "v": body.verdict, "n": body.note, "u": principal.user_id},
        )
        await record(
            session,
            AuditEvent(
                action="action.verdict",
                entity_type="agent_action",
                entity_id=str(action_id),
                new_value={"verdict": body.verdict},
                actor_user_id=principal.user_id,
                actor_role=principal.role,
            ),
        )
        return await action_out(session, action_id)
