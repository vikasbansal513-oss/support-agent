from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.contracts import AdminHealthOut
from app.core import timeutil
from app.core.db import session_scope

Factory = async_sessionmaker[AsyncSession]


async def compute_health(factory: Factory) -> AdminHealthOut:
    db_status: str = "ok"
    worker_last_seen = None
    queue_depth = 0
    jobs_failed_today = 0
    try:
        async with session_scope(factory, super_admin=True) as session:
            await session.execute(text("SELECT 1"))
            worker_last_seen = (
                await session.execute(
                    text("SELECT last_seen_at FROM heartbeats WHERE name = 'worker'")
                )
            ).scalar_one_or_none()
            queue_depth = (
                await session.execute(
                    text("SELECT count(*) FROM jobs WHERE status IN ('queued', 'deferred')")
                )
            ).scalar_one()
            jobs_failed_today = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM jobs WHERE status = 'needs_review'"
                        " AND (updated_at AT TIME ZONE 'Asia/Kolkata')::date"
                        " = (now() AT TIME ZONE 'Asia/Kolkata')::date"
                    )
                )
            ).scalar_one()
    except Exception:  # noqa: BLE001
        db_status = "down"

    seconds_since = None
    if worker_last_seen is not None:
        seconds_since = int((timeutil.utc_now() - worker_last_seen).total_seconds())

    degraded = db_status == "down" or (seconds_since is not None and seconds_since > 180)
    return AdminHealthOut(
        status="degraded" if degraded else "ok",
        db=db_status,
        worker_last_seen_at=worker_last_seen,
        worker_seconds_since=seconds_since,
        queue_depth=int(queue_depth),
        jobs_failed_today=int(jobs_failed_today),
    )
