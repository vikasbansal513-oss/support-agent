import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
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


_SELECT = (
    "SELECT a.id, a.tenant_id, t.name AS tenant_name, a.kind, a.severity, a.message,"
    " a.status, a.service, a.affected_agent, a.error_id, a.root_cause, a.created_at,"
    " a.acknowledged_at, a.resolution_notes, a.resolved_at,"
    " EXTRACT(epoch FROM (a.resolved_at - a.created_at)) / 60 AS resolution_minutes"
    " FROM alerts a LEFT JOIN tenants t ON t.id = a.tenant_id"
)


def _out(r: object) -> AlertOut:
    return AlertOut(
        id=r.id,  # type: ignore[attr-defined]
        tenant_id=r.tenant_id,  # type: ignore[attr-defined]
        tenant_name=r.tenant_name,  # type: ignore[attr-defined]
        kind=r.kind,  # type: ignore[attr-defined]
        severity=r.severity,  # type: ignore[attr-defined]
        message=r.message,  # type: ignore[attr-defined]
        status=r.status,  # type: ignore[attr-defined]
        service=r.service,  # type: ignore[attr-defined]
        affected_agent=r.affected_agent,  # type: ignore[attr-defined]
        error_id=r.error_id,  # type: ignore[attr-defined]
        root_cause=r.root_cause,  # type: ignore[attr-defined]
        created_at=r.created_at,  # type: ignore[attr-defined]
        acknowledged_at=r.acknowledged_at,  # type: ignore[attr-defined]
        resolution_notes=r.resolution_notes,  # type: ignore[attr-defined]
        resolved_at=r.resolved_at,  # type: ignore[attr-defined]
        resolution_minutes=(
            round(float(r.resolution_minutes), 1)  # type: ignore[attr-defined]
            if r.resolution_minutes is not None  # type: ignore[attr-defined]
            else None
        ),
    )


@router.get("")
async def list_alerts(
    status: str | None = Query(default="open", pattern="^(open|acknowledged|resolved)$"),
    severity: str | None = Query(default=None, pattern="^(info|warning|high|critical)$"),
    tenant_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Page:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        await alerts.scan_and_upsert(session)
        rows = (
            await session.execute(
                text(
                    f"{_SELECT}"
                    " WHERE (CAST(:status AS text) IS NULL OR a.status = :status)"
                    " AND (CAST(:severity AS text) IS NULL OR a.severity = :severity)"
                    " AND (CAST(:tenant AS uuid) IS NULL OR a.tenant_id = CAST(:tenant AS uuid))"
                    " ORDER BY a.severity = 'critical' DESC, a.severity = 'high' DESC,"
                    " a.created_at DESC LIMIT :limit"
                ),
                {
                    "status": status,
                    "severity": severity,
                    "tenant": str(tenant_id) if tenant_id else None,
                    "limit": limit,
                },
            )
        ).all()
    return Page(items=[_out(r) for r in rows], next_cursor=None)


@router.get("/stream")
async def stream_alerts(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StreamingResponse:
    """Server-Sent Events so a critical alert reaches the admin without them opening the
    Alerts page. No new broker/queue: Cloud Run can run several API instances, so an
    in-process pub/sub would miss events raised by another instance; polling the `alerts`
    table (same scan_and_upsert the Alerts page already runs) is the one mechanism that is
    correct across instances without adding infrastructure. Capped at 10 minutes per
    connection -- the browser's EventSource reconnects automatically after that."""

    async def events() -> AsyncIterator[str]:
        seen: dict[uuid.UUID, str] = {}
        for _ in range(120):  # 120 * 5s = 10 minutes
            async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
                await alerts.scan_and_upsert(session)
                rows = (
                    await session.execute(
                        text(
                            f"{_SELECT} WHERE a.status = 'open' ORDER BY a.created_at DESC LIMIT 200"
                        )
                    )
                ).all()
            changed = [r for r in rows if seen.get(r.id) != r.status]
            if changed or seen == {}:
                seen = {r.id: r.status for r in rows}
                payload = [_out(r).model_dump(mode="json") for r in rows]
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AlertOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        row = (
            await session.execute(text("SELECT status FROM alerts WHERE id = :i"), {"i": alert_id})
        ).one_or_none()
        if row is None:
            raise AppError("E-NF-001")
        if row.status == "open":
            await session.execute(
                text(
                    "UPDATE alerts SET status = 'acknowledged', acknowledged_at = now(),"
                    " acknowledged_by = :u, updated_at = now() WHERE id = :i"
                ),
                {"u": admin.user_id, "i": alert_id},
            )
            await record(
                session,
                AuditEvent(
                    action="admin.alert_acknowledged",
                    entity_type="alert",
                    entity_id=str(alert_id),
                    old_value={"status": row.status},
                    new_value={"status": "acknowledged"},
                    actor_user_id=admin.user_id,
                    actor_role="super_admin",
                ),
            )
        out = (await session.execute(text(f"{_SELECT} WHERE a.id = :i"), {"i": alert_id})).one()
    return _out(out)


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
                " resolved_by = :u, resolution_notes = :n WHERE id = :i"
            ),
            {"u": admin.user_id, "n": body.reason, "i": alert_id},
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
        out = (await session.execute(text(f"{_SELECT} WHERE a.id = :i"), {"i": alert_id})).one()
    return _out(out)
