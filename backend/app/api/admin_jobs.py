import base64
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import JobOut, Page
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

router = APIRouter(prefix="/admin/jobs", tags=["admin-jobs"])
Factory = async_sessionmaker[AsyncSession]
JobStatus = Literal["queued", "deferred", "processing", "done", "failed", "needs_review"]

_LIST = text(
    "SELECT id, tenant_id, kind, status, attempts, error_code, error_id, created_at FROM jobs"
    " WHERE (CAST(:status AS text) IS NULL OR status = :status)"
    " AND (CAST(:tenant AS uuid) IS NULL OR tenant_id = CAST(:tenant AS uuid))"
    " AND (CAST(:c_at AS timestamptz) IS NULL OR (created_at, id) < (:c_at, CAST(:c_id AS uuid)))"
    " ORDER BY created_at DESC, id DESC LIMIT :limit"
)


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        stamp, _, ident = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError("E-VAL-001", detail="bad cursor") from exc


@router.get("")
async def list_jobs(
    status: JobStatus | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Page:
    params: dict[str, Any] = {
        "limit": limit + 1,
        "status": status,
        "tenant": str(tenant_id) if tenant_id else None,
        "c_at": None,
        "c_id": None,
    }
    if cursor:
        c_at, c_id = _decode(cursor)
        params["c_at"], params["c_id"] = c_at, str(c_id)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        rows = (await session.execute(_LIST, params)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        raw = f"{last.created_at.isoformat()}|{last.id}"
        next_cursor = base64.urlsafe_b64encode(raw.encode()).decode()
    items = [
        JobOut(
            id=r.id,
            tenant_id=r.tenant_id,
            kind=r.kind,
            status=r.status,
            attempts=r.attempts,
            error_code=r.error_code,
            error_id=r.error_id,
            created_at=r.created_at,
        )
        for r in page
    ]
    return Page(items=items, next_cursor=next_cursor)
