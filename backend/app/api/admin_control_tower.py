from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import ControlTowerOut, TenantRankOut
from app.core.db import get_session_factory, session_scope
from app.core.health import compute_health

router = APIRouter(prefix="/admin", tags=["admin-control-tower"])
Factory = async_sessionmaker[AsyncSession]


@router.get("/control-tower")
async def control_tower(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ControlTowerOut:
    health = await compute_health(factory)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        runs = (
            await session.execute(
                text(
                    "SELECT count(*) AS total, count(*) FILTER (WHERE status = 'failed') AS failed,"
                    " count(*) FILTER (WHERE status = 'skipped') AS skipped FROM agent_runs"
                    " WHERE started_at >= now() - interval '24 hours'"
                )
            )
        ).one()
        error_pct = round(runs.failed * 100 / runs.total, 1) if runs.total else 0.0
        p95 = (
            await session.execute(
                text(
                    "SELECT CAST(percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)"
                    " AS integer) FROM llm_calls WHERE created_at >= now() - interval '24 hours'"
                )
            )
        ).scalar_one_or_none()
        failed_today = (
            await session.execute(
                text(
                    "SELECT count(*) FROM jobs WHERE status = 'failed'"
                    " AND (updated_at AT TIME ZONE 'Asia/Kolkata')::date"
                    " = (now() AT TIME ZONE 'Asia/Kolkata')::date"
                )
            )
        ).scalar_one()
        avg_7d = (
            await session.execute(
                text(
                    "SELECT COALESCE(avg(daily), 0) FROM ("
                    " SELECT (updated_at AT TIME ZONE 'Asia/Kolkata')::date AS d, count(*) AS daily"
                    " FROM jobs WHERE status = 'failed'"
                    " AND updated_at >= now() - interval '7 days' GROUP BY d) t"
                )
            )
        ).scalar_one()
        needs_review_today = (
            await session.execute(
                text(
                    "SELECT count(*) FROM jobs WHERE status = 'needs_review'"
                    " AND (updated_at AT TIME ZONE 'Asia/Kolkata')::date"
                    " = (now() AT TIME ZONE 'Asia/Kolkata')::date"
                )
            )
        ).scalar_one()
        needs_review_avg_7d = (
            await session.execute(
                text(
                    "SELECT COALESCE(avg(daily), 0) FROM ("
                    " SELECT (updated_at AT TIME ZONE 'Asia/Kolkata')::date AS d, count(*) AS daily"
                    " FROM jobs WHERE status = 'needs_review'"
                    " AND updated_at >= now() - interval '7 days' GROUP BY d) t"
                )
            )
        ).scalar_one()
        rank_rows = (
            await session.execute(
                text(
                    "SELECT t.id, t.name,"
                    " count(*) FILTER (WHERE a.status = 'sent') AS handled,"
                    " count(*) FILTER (WHERE a.action_type = 'handoff_notify') AS handed_off"
                    " FROM tenants t JOIN agent_actions a ON a.tenant_id = t.id"
                    " WHERE a.created_at >= now() - interval '7 days'"
                    " GROUP BY t.id, t.name HAVING count(*) > 0"
                    " ORDER BY (count(*) FILTER (WHERE a.action_type = 'handoff_notify'))::numeric"
                    " / NULLIF(count(*), 0) ASC LIMIT 10"
                )
            )
        ).all()
    top_tenants = [
        TenantRankOut(
            tenant_id=r.id,
            name=r.name,
            handled=int(r.handled),
            handed_off=int(r.handed_off),
            handoff_rate_percent=(
                round(r.handed_off * 100 / (r.handled + r.handed_off), 1)
                if (r.handled + r.handed_off) > 0
                else 0.0
            ),
        )
        for r in rank_rows
    ]
    return ControlTowerOut(
        health=health,
        error_rate_percent=error_pct,
        skipped_count=int(runs.skipped),
        p95_latency_ms=p95,
        failed_jobs_today=int(failed_today),
        failed_jobs_avg_7d=round(float(avg_7d), 1),
        needs_review_jobs_today=int(needs_review_today),
        needs_review_jobs_avg_7d=round(float(needs_review_avg_7d), 1),
        top_tenants=top_tenants,
    )
