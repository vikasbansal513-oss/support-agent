import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import AlertOut, Page, ReasonIn
from app.core import alerts
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

router = APIRouter(prefix="/admin/alerts", tags=["admin-alerts"])
Factory = async_sessionmaker[AsyncSession]


@router.get("")
async def list_alerts(
    status: str | None = Query(default="open", pattern="^(open|resolved)$"),
    limit: int = Query(default=100, ge=1, le=500),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Page:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        await alerts.scan_and_upsert(session)
        rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, kind, severity, message, status, created_at,"
                    " resolved_at FROM alerts"
                    " WHERE (CAST(:status AS text) IS NULL OR status = :status)"
                    " ORDER BY severity = 'critical' DESC, created_at DESC LIMIT :limit"
                ),
                {"status": status, "limit": limit},
            )
        ).all()
    return Page(
        items=[
            AlertOut(
                id=r.id,
                tenant_id=r.tenant_id,
                kind=r.kind,
                severity=r.severity,
                message=r.message,
                status=r.status,
                created_at=r.created_at,
                resolved_at=r.resolved_at,
            )
            for r in rows
        ],
        next_cursor=None,
    )


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: uuid.UUID,
    body: ReasonIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AlertOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        row = (
            await session.execute(
                text("SELECT kind, status FROM alerts WHERE id = :i"), {"i": alert_id}
            )
        ).one_or_none()
        if row is None:
            raise AppError("E-NF-001")
        await session.execute(
            text(
                "UPDATE alerts SET status = 'resolved', resolved_at = now(), updated_at = now(),"
                " resolved_by = :u WHERE id = :i"
            ),
            {"u": admin.user_id, "i": alert_id},
        )
        await record(
            session,
            AuditEvent(
                action="admin.alert_resolved",
                entity_type="alert",
                entity_id=str(alert_id),
                old_value={"status": row.status},
                new_value={"status": "resolved"},
                reason=body.reason,
                actor_user_id=admin.user_id,
                actor_role="super_admin",
            ),
        )
        out = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, kind, severity, message, status, created_at,"
                    " resolved_at FROM alerts WHERE id = :i"
                ),
                {"i": alert_id},
            )
        ).one()
    return AlertOut(
        id=out.id,
        tenant_id=out.tenant_id,
        kind=out.kind,
        severity=out.severity,
        message=out.message,
        status=out.status,
        created_at=out.created_at,
        resolved_at=out.resolved_at,
    )
