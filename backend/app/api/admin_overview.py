from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import AdminHealthOut, CallsByDayOut, OverviewOut
from app.core import timeutil
from app.core.db import get_session_factory, session_scope
from app.core.health import compute_health

router = APIRouter(prefix="/admin", tags=["admin-overview"])
Factory = async_sessionmaker[AsyncSession]


@router.get("/overview")
async def overview(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> OverviewOut:
    since = timeutil.to_ist(timeutil.utc_now()).date() - timedelta(days=6)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        tenants = (
            await session.execute(
                text(
                    "SELECT count(*) AS total,"
                    " count(*) FILTER (WHERE status = 'active') AS active,"
                    " count(*) FILTER (WHERE status = 'pending_approval') AS pending,"
                    " count(*) FILTER (WHERE status = 'paused') AS paused"
                    " FROM tenants"
                )
            )
        ).one()
        handoffs_open = (
            await session.execute(text("SELECT count(*) FROM handoffs WHERE status = 'open'"))
        ).scalar_one()
        jobs_failed = (
            await session.execute(text("SELECT count(*) FROM jobs WHERE status = 'needs_review'"))
        ).scalar_one()
        today = (
            await session.execute(
                text(
                    "SELECT count(*) AS calls, COALESCE(sum(est_cost_micro), 0) AS cost_micro"
                    " FROM llm_calls WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date"
                    " = (now() AT TIME ZONE 'Asia/Kolkata')::date"
                )
            )
        ).one()
        by_day = (
            await session.execute(
                text(
                    "SELECT to_char(created_at AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD') AS day,"
                    " count(*) AS calls FROM llm_calls"
                    " WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date >= :since"
                    " GROUP BY day ORDER BY day"
                ),
                {"since": since},
            )
        ).all()
    return OverviewOut(
        tenants_total=int(tenants.total),
        tenants_active=int(tenants.active),
        tenants_pending=int(tenants.pending),
        tenants_paused=int(tenants.paused),
        handoffs_open=int(handoffs_open),
        jobs_failed=int(jobs_failed),
        today_calls=int(today.calls),
        today_cost_micro=int(today.cost_micro),
        calls_by_day=[CallsByDayOut(day=r.day, calls=int(r.calls)) for r in by_day],
    )


@router.get("/health")
async def health(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AdminHealthOut:
    return await compute_health(factory)
