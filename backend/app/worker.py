import argparse
import asyncio
import logging
import os
import socket
import time
import uuid
from datetime import datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import runner
from app.core import timeutil
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError, new_error_id
from app.core.logging import configure_logging, run_id_var
from app.core.monitoring import init_monitoring
from app.jobs import ops, queue
from app.jobs.handlers import EXHAUSTED, JobContext
from app.jobs.handlers import HANDLERS as BASE_HANDLERS

HANDLERS = {**BASE_HANDLERS, **ops.HANDLERS}
logger = logging.getLogger("app.worker")
Factory = async_sessionmaker[AsyncSession]
IDLE_SLEEP_SECONDS = 2.0


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


async def heartbeat(factory: Factory, name: str = "worker") -> None:
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text(
                "INSERT INTO heartbeats (name, last_seen_at) VALUES (:n, :t)"
                " ON CONFLICT (name) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at"
            ),
            {"n": name, "t": timeutil.utc_now()},
        )


def minute_bucket(now: datetime, minutes: int = 1) -> str:
    epoch_minutes = int(now.timestamp() // 60)
    return str(epoch_minutes // minutes)


async def schedule_periodic(factory: Factory, now: datetime | None = None) -> int:
    moment = now or timeutil.utc_now()
    created = 0
    async with session_scope(factory, super_admin=True) as session:
        tenants = (
            await session.execute(
                text(
                    "SELECT t.id, i.kind FROM tenants t JOIN integrations i ON i.tenant_id = t.id"
                    " WHERE t.status = 'active' AND i.status <> 'disabled'"
                )
            )
        ).all()
        for row in tenants:
            if row.kind == "gmail":
                job = await queue.enqueue(
                    session,
                    row.id,
                    "fetch_gmail",
                    idempotency_key=f"gmail:{minute_bucket(moment, 15)}",
                    max_attempts=1,
                )
                created += job is not None
        for tenant_id in {row.id for row in tenants}:
            job = await queue.enqueue(
                session,
                tenant_id,
                "integration_health",
                idempotency_key=f"health:{minute_bucket(moment, 60)}",
                max_attempts=1,
            )
            created += job is not None
        active = (
            await session.execute(
                text(
                    "SELECT t.id,"
                    " EXISTS (SELECT 1 FROM handoffs h WHERE h.tenant_id = t.id"
                    "  AND h.status IN ('open','claimed')) AS handoffs,"
                    " EXISTS (SELECT 1 FROM jobs j WHERE j.tenant_id = t.id"
                    "  AND j.kind = 'process_inbound' AND j.created_at > :recent) AS inbound,"
                    " EXISTS (SELECT 1 FROM privacy_requests p WHERE p.tenant_id = t.id"
                    "  AND p.status = 'open') AS privacy,"
                    " EXISTS (SELECT 1 FROM agent_actions a WHERE a.tenant_id = t.id"
                    "  AND a.status IN ('awaiting_customer','needs_review','approved')) AS writes,"
                    " EXISTS (SELECT 1 FROM agent_actions a WHERE a.tenant_id = t.id"
                    "  AND a.status = 'pending_approval') AS approvals"
                    " FROM tenants t WHERE t.status = 'active'"
                ),
                {"recent": moment - timedelta(days=1)},
            )
        ).all()
        for tenant in active:
            due: list[tuple[str, int]] = [("retention", 1440)]
            if tenant.handoffs:
                due.append(("sla_check", 15))
            if tenant.inbound:
                due += [("llm_latency_check", 60), ("unanswered_alert", 60)]
            if tenant.approvals:
                due.append(("approval_sla", 30))
            if tenant.writes:
                due.append(("write_upkeep", 2))
            if tenant.privacy:
                due.append(("privacy_due", 1440))
            for kind, minutes in due:
                job = await queue.enqueue(
                    session,
                    tenant.id,
                    kind,
                    idempotency_key=f"{kind}:{minute_bucket(moment, minutes)}",
                    max_attempts=1,
                )
                created += job is not None
    return created


async def run_job(
    factory: Factory,
    job: queue.Job,
    worker: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    handler = HANDLERS.get(job.kind)
    ctx = JobContext(factory=factory, job=job, transport=transport)
    token = run_id_var.set(str(job.id))
    try:
        if handler is None:
            raise AppError("E-SYS-001", detail=f"unknown job kind {job.kind}")
        result = await handler(ctx)
        await queue.finish(factory, job.id, worker, result)
        return "done"
    except Exception as exc:
        error_id = new_error_id()
        code = exc.code if isinstance(exc, AppError) else type(exc).__name__
        logger.error(
            "job failed",
            extra={"error_id": error_id, "error_code": code, "job_kind": job.kind},
        )
        outcome = await queue.fail(factory, job, worker, code, str(exc), error_id)
        if outcome.needs_review and job.kind in EXHAUSTED:
            await EXHAUSTED[job.kind](ctx)
        return "needs_review" if outcome.needs_review else "retry"
    finally:
        run_id_var.reset(token)


async def drain_queue(factory: Factory, job_id: uuid.UUID, worker: str) -> bool:
    """Process one just-enqueued job immediately, on the caller's own request, instead of
    waiting for the next scheduled worker tick. A webhook calls this (gated behind
    settings.instant_drain_enabled) so a reply goes out instantly on cheap request-based
    billing; the scheduled worker tick stays as the fallback whenever this is skipped,
    races with a tick, or fails - draining never replaces the queue, only speeds it up."""
    settings = get_settings()
    job = await queue.claim_by_id(factory, job_id, worker, settings.job_lease_seconds)
    if job is None:
        return False
    outcome = await run_job(factory, job, worker)
    return outcome == "done"


async def run_worker(
    factory: Factory,
    max_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
    worker: str | None = None,
    idle_sleep: float = IDLE_SLEEP_SECONDS,
    exit_when_idle: bool = False,
) -> dict[str, int]:
    settings = get_settings()
    me = worker or worker_id()
    deadline = time.monotonic() + max_seconds
    stats = {"done": 0, "retry": 0, "needs_review": 0, "reaped": 0}
    await heartbeat(factory)
    await schedule_periodic(factory)
    stats["reaped"] = len(await queue.reap_expired(factory))
    while time.monotonic() < deadline:
        job = await queue.claim(factory, me, settings.job_lease_seconds, settings.job_tenant_limit)
        if job is None:
            if exit_when_idle or time.monotonic() + idle_sleep >= deadline:
                break
            await asyncio.sleep(idle_sleep)
            continue
        outcome = await run_job(factory, job, me, transport)
        stats[outcome] += 1
        await heartbeat(factory)
    return stats


def main() -> None:
    configure_logging()
    init_monitoring("worker")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-seconds", type=float, default=float(get_settings().worker_max_seconds)
    )
    args = parser.parse_args()
    runner.install()
    stats = asyncio.run(run_worker(get_session_factory(), args.max_seconds, exit_when_idle=True))
    logger.info("worker finished", extra={"stats": stats})


if __name__ == "__main__":
    main()
