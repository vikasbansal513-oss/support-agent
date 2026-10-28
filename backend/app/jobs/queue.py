import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import timeutil
from app.core.db import session_scope
from app.core.masking import redact_text

Factory = async_sessionmaker[AsyncSession]
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 1800


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def backoff_seconds(attempts: int) -> int:
    return int(min(BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)), BACKOFF_CAP_SECONDS))


_ENQUEUE = text(
    "INSERT INTO jobs (tenant_id, kind, queue, payload, idempotency_key, max_attempts, run_after,"
    " created_by) VALUES (:t, :k, :q, CAST(:p AS jsonb), :idem, :max, :after, :by)"
    " ON CONFLICT (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING"
    " RETURNING id"
)


async def enqueue(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    queue: str = "default",
    run_after: datetime | None = None,
    max_attempts: int = 3,
    created_by: uuid.UUID | None = None,
) -> uuid.UUID | None:
    row = (
        await session.execute(
            _ENQUEUE,
            {
                "t": tenant_id,
                "k": kind,
                "q": queue,
                "p": json.dumps(payload or {}),
                "idem": idempotency_key,
                "max": max_attempts,
                "after": run_after or timeutil.utc_now(),
                "by": created_by,
            },
        )
    ).first()
    return None if row is None else uuid.UUID(str(row.id))


_CLAIM = text(
    "WITH c AS (SELECT j.id FROM jobs j WHERE j.status IN ('queued','deferred')"
    " AND j.run_after <= :now AND (CAST(:kinds AS text[]) IS NULL OR j.kind = ANY(:kinds))"
    " AND (SELECT count(*) FROM jobs p WHERE p.tenant_id = j.tenant_id AND p.status = 'processing')"
    " < :tenant_limit ORDER BY (j.kind = 'process_inbound') DESC, j.run_after, j.created_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED)"
    " UPDATE jobs SET status = 'processing', attempts = attempts + 1, lease_owner = :worker,"
    " lease_expires_at = :lease, started_at = :now, updated_at = :now FROM c WHERE jobs.id = c.id"
    " RETURNING jobs.id, jobs.tenant_id, jobs.kind, jobs.payload, jobs.attempts, jobs.max_attempts"
)


async def claim(
    factory: Factory,
    worker: str,
    lease_seconds: int,
    tenant_limit: int,
    kinds: list[str] | None = None,
) -> Job | None:
    now = timeutil.utc_now()
    async with session_scope(factory, super_admin=True) as session:
        row = (
            await session.execute(
                _CLAIM,
                {
                    "now": now,
                    "kinds": kinds,
                    "tenant_limit": tenant_limit,
                    "worker": worker,
                    "lease": now + timedelta(seconds=lease_seconds),
                },
            )
        ).first()
    if row is None:
        return None
    return Job(row.id, row.tenant_id, row.kind, dict(row.payload), row.attempts, row.max_attempts)


_CLAIM_BY_ID = text(
    "UPDATE jobs SET status = 'processing', attempts = attempts + 1, lease_owner = :worker,"
    " lease_expires_at = :lease, started_at = :now, updated_at = :now"
    " WHERE id = :id AND status IN ('queued','deferred') AND run_after <= :now"
    " RETURNING id, tenant_id, kind, payload, attempts, max_attempts"
)


async def claim_by_id(
    factory: Factory, job_id: uuid.UUID, worker: str, lease_seconds: int
) -> Job | None:
    """Claim one specific job (by id) right after enqueueing it, for inline/same-request
    processing. Returns None if it is already claimed (e.g. a worker tick got it first) or not
    due yet; the normal worker loop remains the fallback in that case."""
    now = timeutil.utc_now()
    async with session_scope(factory, super_admin=True) as session:
        row = (
            await session.execute(
                _CLAIM_BY_ID,
                {
                    "id": job_id,
                    "worker": worker,
                    "lease": now + timedelta(seconds=lease_seconds),
                    "now": now,
                },
            )
        ).first()
    if row is None:
        return None
    return Job(row.id, row.tenant_id, row.kind, dict(row.payload), row.attempts, row.max_attempts)


async def extend_lease(
    factory: Factory, job_id: uuid.UUID, worker: str, lease_seconds: int
) -> bool:
    now = timeutil.utc_now()
    async with session_scope(factory, super_admin=True) as session:
        result = (
            await session.execute(
                text(
                    "UPDATE jobs SET lease_expires_at = :lease, updated_at = :now WHERE id = :i"
                    " AND lease_owner = :w AND status = 'processing' RETURNING 1"
                ),
                {
                    "lease": now + timedelta(seconds=lease_seconds),
                    "now": now,
                    "i": job_id,
                    "w": worker,
                },
            )
        ).first()
    return result is not None


async def finish(
    factory: Factory, job_id: uuid.UUID, worker: str, result: dict[str, Any] | None = None
) -> bool:
    now = timeutil.utc_now()
    async with session_scope(factory, super_admin=True) as session:
        row = (
            await session.execute(
                text(
                    "UPDATE jobs SET status = 'done', finished_at = :now, updated_at = :now,"
                    " lease_owner = NULL, lease_expires_at = NULL, result = CAST(:r AS jsonb),"
                    " duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (:now - started_at)) * 1000)::bigint)"
                    " WHERE id = :i AND lease_owner = :w AND status = 'processing' RETURNING 1"
                ),
                {"now": now, "r": json.dumps(result or {}), "i": job_id, "w": worker},
            )
        ).first()
    return row is not None


@dataclass(frozen=True)
class FailOutcome:
    recorded: bool
    needs_review: bool


async def fail(
    factory: Factory,
    job: Job,
    worker: str,
    error_code: str,
    error_message: str,
    error_id: str,
    retryable: bool = True,
) -> FailOutcome:
    now = timeutil.utc_now()
    exhausted = (not retryable) or job.attempts >= job.max_attempts
    status = "needs_review" if exhausted else "queued"
    after = now + timedelta(seconds=backoff_seconds(job.attempts))
    async with session_scope(factory, super_admin=True) as session:
        row = (
            await session.execute(
                text(
                    "UPDATE jobs SET status = :s, run_after = :after, lease_owner = NULL,"
                    " lease_expires_at = NULL, error_code = :c, error_message = :m, error_id = :e,"
                    " updated_at = :now, finished_at = CASE WHEN CAST(:s AS text) = 'needs_review'"
                    " THEN CAST(:now AS timestamptz) ELSE NULL END WHERE id = :i AND lease_owner = :w AND status = 'processing'"
                    " RETURNING 1"
                ),
                {
                    "s": status,
                    "after": after,
                    "c": error_code,
                    "m": redact_text(error_message)[:500],
                    "e": error_id,
                    "now": now,
                    "i": job.id,
                    "w": worker,
                },
            )
        ).first()
    recorded = row is not None
    if recorded and exhausted:
        await notify_needs_review(factory, job.tenant_id, job.kind, error_id)
    return FailOutcome(recorded, recorded and exhausted)


async def notify_needs_review(
    factory: Factory, tenant_id: uuid.UUID, kind: str, error_id: str
) -> None:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO notifications (audience, title, body) VALUES"
                " ('admin', 'Job needs review', :b)"
            ),
            {"b": f"{kind} job fail hua. Error ID: {error_id}"},
        )


async def reap_expired(factory: Factory) -> list[Job]:
    now = timeutil.utc_now()
    reaped: list[Job] = []
    async with session_scope(factory, super_admin=True) as session:
        rows = (
            await session.execute(
                text(
                    "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'needs_review'"
                    " ELSE 'queued' END, run_after = :now, lease_owner = NULL, lease_expires_at = NULL,"
                    " error_code = 'JOB_LEASE_EXPIRED', updated_at = :now WHERE status = 'processing'"
                    " AND lease_expires_at < :now RETURNING id, tenant_id, kind, payload, attempts,"
                    " max_attempts, status"
                ),
                {"now": now},
            )
        ).all()
    for row in rows:
        job = Job(
            row.id, row.tenant_id, row.kind, dict(row.payload), row.attempts, row.max_attempts
        )
        reaped.append(job)
        if row.status == "needs_review":
            await notify_needs_review(factory, row.tenant_id, row.kind, "lease-expired")
    return reaped
