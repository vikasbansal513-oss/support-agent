import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import (
    AccuracyOut,
    AgentTaskDetailOut,
    AgentTaskListOut,
    AgentTaskStatus,
    HistoricalOut,
    PagePerfOut,
    ThresholdOut,
    ThresholdsIn,
    TrafficOut,
)
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.monitoring import queries

router = APIRouter(prefix="/admin/monitoring", tags=["admin-monitoring"])
Factory = async_sessionmaker[AsyncSession]
Range = Literal["24h", "7d", "30d"]


@router.get("/agent-tasks")
async def agent_tasks(
    tenant_id: uuid.UUID | None = None,
    category: str | None = None,
    status: AgentTaskStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AgentTaskListOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await queries.agent_task_list(
            session,
            tenant_id=tenant_id,
            category=category,
            status=status,
            date_from=date_from.isoformat() if date_from else None,
            date_to=date_to.isoformat() if date_to else None,
            page=page,
            page_size=page_size,
        )


@router.get("/agent-tasks/{run_id}")
async def agent_task_detail(
    run_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AgentTaskDetailOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        detail = await queries.agent_task_detail(session, run_id)
    if detail is None:
        raise AppError("E-NF-001")
    return detail


@router.get("/accuracy")
async def accuracy(
    window: Range = "7d",
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AccuracyOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await queries.accuracy(session, window)


@router.get("/page-performance")
async def page_performance(
    tenant_id: uuid.UUID | None = None,
    days: int = Query(default=7, ge=1, le=30),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PagePerfOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await queries.page_performance(session, tenant_id, days)


@router.get("/traffic")
async def traffic(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TrafficOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await queries.traffic(session)


@router.get("/historical")
async def historical(
    range: Range = "7d",  # noqa: A002
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> HistoricalOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await queries.historical(session, range, tenant_id)


@router.get("/thresholds")
async def list_thresholds(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[ThresholdOut]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        rows = (
            await session.execute(text("SELECT key, value FROM monitoring_thresholds ORDER BY key"))
        ).all()
    return [ThresholdOut(key=r.key, value=float(r.value)) for r in rows]


@router.put("/thresholds")
async def update_thresholds(
    body: ThresholdsIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[ThresholdOut]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        existing = {
            r.key: float(r.value)
            for r in (
                await session.execute(text("SELECT key, value FROM monitoring_thresholds"))
            ).all()
        }
        for key, value in body.values.items():
            if key not in existing:
                raise AppError("E-VAL-001", detail=f"unknown threshold: {key}")
            await session.execute(
                text(
                    "UPDATE monitoring_thresholds SET value = :v, updated_by = :u, updated_at = now()"
                    " WHERE key = :k"
                ),
                {"v": value, "u": admin.user_id, "k": key},
            )
            await record(
                session,
                AuditEvent(
                    action="admin.monitoring_threshold_changed",
                    entity_type="monitoring_threshold",
                    entity_id=key,
                    old_value={"value": existing[key]},
                    new_value={"value": value},
                    actor_user_id=admin.user_id,
                    actor_role="super_admin",
                ),
            )
        rows = (
            await session.execute(text("SELECT key, value FROM monitoring_thresholds ORDER BY key"))
        ).all()
    return [ThresholdOut(key=r.key, value=float(r.value)) for r in rows]
