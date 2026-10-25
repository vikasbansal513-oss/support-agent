import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import timeutil
from app.core.db import session_scope
from app.jobs import queue
from tests.auth.helpers import Clock

Factory = async_sessionmaker[AsyncSession]


async def make_tenant(factory: Factory) -> uuid.UUID:
    tid = uuid.uuid4()
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'T', :s)"),
            {"i": tid, "s": f"q-{tid.hex[:12]}"},
        )
    return tid


async def put(factory: Factory, tenant: uuid.UUID, kind: str, **kw: object) -> uuid.UUID | None:
    async with session_scope(factory, super_admin=True) as session:
        return await queue.enqueue(session, tenant, kind, **kw)  # type: ignore[arg-type]


async def isolate(factory: Factory, tenant: uuid.UUID) -> None:
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text(
                "UPDATE jobs SET status = 'done' WHERE tenant_id <> :t AND status IN ('queued','deferred')"
            ),
            {"t": tenant},
        )


async def test_enqueue_idempotent(runtime_factory: Factory) -> None:
    t = await make_tenant(runtime_factory)
    first = await put(runtime_factory, t, "k", idempotency_key="same")
    second = await put(runtime_factory, t, "k", idempotency_key="same")
    assert first is not None and second is None
    assert (
        await put(runtime_factory, t, "k") is not None
        and await put(runtime_factory, t, "k") is not None
    )


async def test_claim_finish_lease_owner_only(runtime_factory: Factory) -> None:
    t = await make_tenant(runtime_factory)
    await isolate(runtime_factory, t)
    await put(runtime_factory, t, "x", payload={"a": 1})
    job = await queue.claim(runtime_factory, "w1", 120, 5)
    assert job is not None and job.tenant_id == t and job.payload == {"a": 1} and job.attempts == 1
    assert await queue.claim(runtime_factory, "w2", 120, 5) is None
    assert not await queue.finish(runtime_factory, job.id, "w2")
    assert await queue.extend_lease(runtime_factory, job.id, "w1", 300)
    assert not await queue.extend_lease(runtime_factory, job.id, "w2", 300)
    assert await queue.finish(runtime_factory, job.id, "w1", {"ok": True})
    assert not await queue.finish(runtime_factory, job.id, "w1")


async def test_two_workers_never_get_same_job(runtime_factory: Factory) -> None:
    t = await make_tenant(runtime_factory)
    await isolate(runtime_factory, t)
    for i in range(6):
        await put(runtime_factory, t, "x", payload={"i": i})
    claims = await asyncio.gather(
        *[queue.claim(runtime_factory, f"w{i}", 120, 100) for i in range(6)]
    )
    ids = [c.id for c in claims if c is not None]
    assert len(ids) == 6 and len(set(ids)) == 6


async def test_tenant_limit_and_run_after(runtime_factory: Factory) -> None:
    t = await make_tenant(runtime_factory)
    await isolate(runtime_factory, t)
    for _ in range(3):
        await put(runtime_factory, t, "x")
    assert await queue.claim(runtime_factory, "w", 120, 2) is not None
    assert await queue.claim(runtime_factory, "w", 120, 2) is not None
    assert await queue.claim(runtime_factory, "w", 120, 2) is None
    t2 = await make_tenant(runtime_factory)
    await put(runtime_factory, t2, "later", run_after=timeutil.utc_now() + timedelta(hours=1))
    await put(runtime_factory, t2, "now")
    got = await queue.claim(runtime_factory, "w", 120, 2)
    assert got is not None and got.kind == "now"


async def test_kinds_filter(runtime_factory: Factory) -> None:
    t = await make_tenant(runtime_factory)
    await isolate(runtime_factory, t)
    await put(runtime_factory, t, "a")
    assert await queue.claim(runtime_factory, "w", 120, 5, kinds=["b"]) is None
    got = await queue.claim(runtime_factory, "w", 120, 5, kinds=["a", "b"])
    assert got is not None and got.kind == "a"


async def test_fail_backoff_then_needs_review_with_notification(
    runtime_factory: Factory, clock: Clock
) -> None:
    t = await make_tenant(runtime_factory)
    await isolate(runtime_factory, t)
    await put(runtime_factory, t, "x", max_attempts=2)
    job = await queue.claim(runtime_factory, "w", 120, 5)
    assert job is not None
    out = await queue.fail(runtime_factory, job, "w", "BOOM", "bad a@example.com", "ERR-1")
    assert out.recorded and not out.needs_review
    assert await queue.claim(runtime_factory, "w", 120, 5) is None
    clock.now += timedelta(seconds=queue.backoff_seconds(1) + 1)
    job2 = await queue.claim(runtime_factory, "w", 120, 5)
    assert job2 is not None and job2.attempts == 2
    out2 = await queue.fail(runtime_factory, job2, "w", "BOOM", "again", "ERR-2")
    assert out2.needs_review
    async with session_scope(runtime_factory, tenant_id=t) as session:
        status = (
            await session.execute(
                text("SELECT status, error_message FROM jobs WHERE id = :i"), {"i": job.id}
            )
        ).one()
        notes = (await session.execute(text("SELECT body FROM notifications"))).scalars().all()
    assert status.status == "needs_review"
    assert any("ERR-2" in n for n in notes)


async def test_fail_non_retryable_and_wrong_owner(runtime_factory: Factory) -> None:
    t = await make_tenant(runtime_factory)
    await isolate(runtime_factory, t)
    await put(runtime_factory, t, "x")
    job = await queue.claim(runtime_factory, "w", 120, 5)
    assert job is not None
    assert not (await queue.fail(runtime_factory, job, "other", "E", "m", "ERR-3")).recorded
    assert (
        await queue.fail(runtime_factory, job, "w", "E", "m", "ERR-3", retryable=False)
    ).needs_review


async def test_reap_expired(runtime_factory: Factory, clock: Clock) -> None:
    t = await make_tenant(runtime_factory)
    await isolate(runtime_factory, t)
    await put(runtime_factory, t, "x", max_attempts=2)
    job = await queue.claim(runtime_factory, "w", 60, 5)
    assert job is not None
    assert await queue.reap_expired(runtime_factory) == []
    clock.now += timedelta(seconds=61)
    reaped = await queue.reap_expired(runtime_factory)
    assert [r.id for r in reaped] == [job.id]
    again = await queue.claim(runtime_factory, "w2", 60, 5)
    assert again is not None and again.attempts == 2
    clock.now += timedelta(seconds=61)
    await queue.reap_expired(runtime_factory)
    async with session_scope(runtime_factory, super_admin=True) as session:
        status = (
            await session.execute(text("SELECT status FROM jobs WHERE id = :i"), {"i": job.id})
        ).scalar_one()
    assert status == "needs_review"


def test_backoff_growth_capped() -> None:
    assert queue.backoff_seconds(1) == 30 and queue.backoff_seconds(2) == 60
    assert queue.backoff_seconds(30) == queue.BACKOFF_CAP_SECONDS


@pytest.mark.parametrize("n", [1])
async def test_processing_requires_lease(runtime_factory: Factory, n: int) -> None:
    t = await make_tenant(runtime_factory)
    job_id = await put(runtime_factory, t, "x")
    with pytest.raises(Exception, match="check"):
        async with session_scope(runtime_factory, super_admin=True) as session:
            await session.execute(
                text("UPDATE jobs SET status = 'processing' WHERE id = :i"), {"i": job_id}
            )
