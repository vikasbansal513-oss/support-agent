import base64
import csv
import io
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import AuditLogRowOut, Page
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

router = APIRouter(prefix="/admin/audit-log", tags=["admin-audit"])
Factory = async_sessionmaker[AsyncSession]

_LIST = text(
    "SELECT id, occurred_at, tenant_id, actor_user_id, actor_role, action, entity_type,"
    " entity_id, reason FROM audit_log"
    " WHERE (CAST(:tenant AS uuid) IS NULL OR tenant_id = CAST(:tenant AS uuid))"
    " AND (CAST(:action AS text) IS NULL OR action ILIKE '%' || :action || '%')"
    " AND (CAST(:entity_type AS text) IS NULL OR entity_type = :entity_type)"
    " AND (CAST(:since AS timestamptz) IS NULL OR occurred_at >= :since)"
    " AND (CAST(:until AS timestamptz) IS NULL OR occurred_at <= :until)"
    " AND (CAST(:c_at AS timestamptz) IS NULL OR (occurred_at, id) < (:c_at, CAST(:c_id AS uuid)))"
    " ORDER BY occurred_at DESC, id DESC LIMIT :limit"
)


def _params(
    tenant_id: uuid.UUID | None,
    action: str | None,
    entity_type: str | None,
    since: datetime | None,
    until: datetime | None,
) -> dict[str, Any]:
    return {
        "tenant": str(tenant_id) if tenant_id else None,
        "action": action.strip() if action and action.strip() else None,
        "entity_type": entity_type.strip() if entity_type and entity_type.strip() else None,
        "since": since,
        "until": until,
    }


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        stamp, _, ident = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError("E-VAL-001", detail="bad cursor") from exc


@router.get("")
async def list_audit_log(
    tenant_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=120),
    entity_type: str | None = Query(default=None, max_length=60),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Page:
    params = _params(tenant_id, action, entity_type, since, until)
    params.update({"limit": limit + 1, "c_at": None, "c_id": None})
    if cursor:
        c_at, c_id = _decode(cursor)
        params["c_at"], params["c_id"] = c_at, str(c_id)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        rows = (await session.execute(_LIST, params)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        raw = f"{last.occurred_at.isoformat()}|{last.id}"
        next_cursor = base64.urlsafe_b64encode(raw.encode()).decode()
    items = [
        AuditLogRowOut(
            id=r.id,
            occurred_at=r.occurred_at,
            tenant_id=r.tenant_id,
            actor_user_id=r.actor_user_id,
            actor_role=r.actor_role,
            action=r.action,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            reason=r.reason,
        )
        for r in page
    ]
    return Page(items=items, next_cursor=next_cursor)


@router.get("/export.csv")
async def export_audit_log_csv(
    tenant_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=120),
    entity_type: str | None = Query(default=None, max_length=60),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=5000, ge=1, le=20000),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    params = _params(tenant_id, action, entity_type, since, until)
    params.update({"limit": limit, "c_at": None, "c_id": None})
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        rows = (await session.execute(_LIST, params)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "occurred_at",
            "tenant_id",
            "actor_user_id",
            "actor_role",
            "action",
            "entity_type",
            "entity_id",
            "reason",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.occurred_at.isoformat(),
                r.tenant_id,
                r.actor_user_id,
                r.actor_role,
                r.action,
                r.entity_type,
                r.entity_id,
                r.reason or "",
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit-log.csv"},
    )
