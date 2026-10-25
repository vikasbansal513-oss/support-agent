import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from app.jobs import handlers
from app.worker import run_worker
from tests.auth.helpers import MemoryMailer
from tests.channels.helpers import WaTenant, setup_wa_tenant, wa_message

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def wa(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> WaTenant:
    return await setup_wa_tenant(client, mailer, runtime_factory, monkeypatch)


async def drain(factory: Factory) -> dict[str, int]:
    return await run_worker(factory, max_seconds=30, idle_sleep=0.01, exit_when_idle=True)


async def q(factory: Factory, t: WaTenant, sql: str, **params: Any) -> list[Any]:
    async with session_scope(factory, tenant_id=t.tenant_id) as session:
        return list((await session.execute(text(sql), params)).all())


async def test_whatsapp_message_becomes_customer_conversation_message_usage(
    client: httpx.AsyncClient, runtime_factory: Factory, wa: WaTenant
) -> None:
    await client.post(wa.path, json=wa_message("a1", body="Order kahan hai"))
    await drain(runtime_factory)
    customers = await q(runtime_factory, wa, "SELECT phone_e164 FROM customers")
    assert [c.phone_e164 for c in customers] == ["+919876543210"]
    convs = await q(
        runtime_factory, wa, "SELECT status, counted_for_usage, last_inbound_at FROM conversations"
    )
    assert len(convs) == 1 and convs[0].counted_for_usage and convs[0].last_inbound_at
    msgs = await q(runtime_factory, wa, "SELECT direction, sender, content FROM messages")
    assert [(m.direction, m.sender, m.content) for m in msgs] == [
        ("in", "customer", "Order kahan hai")
    ]
    usage = await q(runtime_factory, wa, "SELECT conversation_count FROM usage_counters")
    assert [u.conversation_count for u in usage] == [1]
    ev = await q(runtime_factory, wa, "SELECT status FROM inbound_events")
    assert ev[0].status == "queued"
    jobs = await q(
        runtime_factory, wa, "SELECT status, result FROM jobs WHERE kind = 'process_inbound'"
    )
    assert jobs[0].status == "done" and jobs[0].result == {"stage": "ingested"}


async def test_second_message_same_conversation_no_double_count_and_reprocess_safe(
    client: httpx.AsyncClient, runtime_factory: Factory, wa: WaTenant
) -> None:
    await client.post(wa.path, json=wa_message("b1"))
    await client.post(wa.path, json=wa_message("b2", body="phir se"))
    await drain(runtime_factory)
    assert len(await q(runtime_factory, wa, "SELECT 1 FROM conversations")) == 1
    assert (await q(runtime_factory, wa, "SELECT conversation_count FROM usage_counters"))[
        0
    ].conversation_count == 1
    assert len(await q(runtime_factory, wa, "SELECT 1 FROM messages")) == 2
    await client.post(wa.path, json=wa_message("b2", body="phir se"))
    await drain(runtime_factory)
    assert len(await q(runtime_factory, wa, "SELECT 1 FROM messages")) == 2


async def test_stop_opts_out_and_start_opts_in(
    client: httpx.AsyncClient, runtime_factory: Factory, wa: WaTenant
) -> None:
    await client.post(wa.path, json=wa_message("s1", body="STOP"))
    await drain(runtime_factory)
    assert (
        len(await q(runtime_factory, wa, "SELECT 1 FROM opt_outs WHERE channel = 'whatsapp'")) == 1
    )
    ev = await q(runtime_factory, wa, "SELECT status, skip_reason FROM inbound_events")
    assert (ev[0].status, ev[0].skip_reason) == ("processed", "OPT_OUT")
    await client.post(wa.path, json=wa_message("s2", body="start"))
    await drain(runtime_factory)
    assert await q(runtime_factory, wa, "SELECT 1 FROM opt_outs") == []


async def test_media_only_message_content_marker(
    client: httpx.AsyncClient, runtime_factory: Factory, wa: WaTenant
) -> None:
    await client.post(wa.path, json=wa_message("img1", body="", kind="image"))
    await drain(runtime_factory)
    msgs = await q(runtime_factory, wa, "SELECT content FROM messages")
    assert msgs[0].content == "[image]"


async def test_flood_skipped(
    client: httpx.AsyncClient,
    runtime_factory: Factory,
    wa: WaTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("CUSTOMER_FLOOD_PER_MINUTE", "3")
    get_settings.cache_clear()
    for i in range(5):
        await client.post(wa.path, json=wa_message(f"f{i}"))
    await drain(runtime_factory)
    assert len(await q(runtime_factory, wa, "SELECT 1 FROM messages")) == 3
    skipped = await q(
        runtime_factory, wa, "SELECT skip_reason FROM inbound_events WHERE status = 'skipped'"
    )
    assert [s.skip_reason for s in skipped] == ["CUSTOMER_FLOOD"] * 2


async def test_inactive_tenant_skipped(
    client: httpx.AsyncClient, runtime_factory: Factory, wa: WaTenant
) -> None:
    await client.post(wa.path, json=wa_message("p1"))
    async with session_scope(runtime_factory, super_admin=True) as session:
        await session.execute(
            text("UPDATE tenants SET status = 'paused' WHERE id = :i"), {"i": wa.tenant_id}
        )
    await drain(runtime_factory)
    ev = await q(runtime_factory, wa, "SELECT skip_reason FROM inbound_events")
    assert ev[0].skip_reason == "TENANT_INACTIVE"
    assert await q(runtime_factory, wa, "SELECT 1 FROM messages") == []


async def test_runner_hook_called_with_context(
    client: httpx.AsyncClient, runtime_factory: Factory, wa: WaTenant
) -> None:
    seen: list[tuple[uuid.UUID, str]] = []

    async def runner(
        ctx: handlers.JobContext,
        tenant_id: uuid.UUID,
        event_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        inbound: Any,
    ) -> None:
        seen.append((tenant_id, inbound.text))
        async with session_scope(ctx.factory, tenant_id=tenant_id) as session:
            await session.execute(
                text("UPDATE inbound_events SET status = 'processed' WHERE id = :i"),
                {"i": event_id},
            )

    handlers.set_inbound_runner(runner)
    try:
        await client.post(wa.path, json=wa_message("r1", body="hello runner"))
        await drain(runtime_factory)
    finally:
        handlers.set_inbound_runner(None)
    assert seen == [(wa.tenant_id, "hello runner")]
    assert (await q(runtime_factory, wa, "SELECT status FROM inbound_events"))[
        0
    ].status == "processed"


async def test_failing_runner_retries_then_needs_review(
    client: httpx.AsyncClient,
    runtime_factory: Factory,
    wa: WaTenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def runner(*_: Any) -> None:
        raise RuntimeError("agent exploded")

    handlers.set_inbound_runner(runner)
    try:
        await client.post(wa.path, json=wa_message("x1"))
        for _ in range(3):
            await drain(runtime_factory)
            async with session_scope(runtime_factory, super_admin=True) as session:
                await session.execute(
                    text(
                        "UPDATE jobs SET run_after = now() WHERE status = 'queued' AND tenant_id = :t"
                    ),
                    {"t": wa.tenant_id},
                )
    finally:
        handlers.set_inbound_runner(None)
    job = (
        await q(
            runtime_factory,
            wa,
            "SELECT status, attempts, error_code FROM jobs WHERE kind = 'process_inbound'",
        )
    )[0]
    assert (job.status, job.attempts, job.error_code) == ("needs_review", 3, "RuntimeError")
    ev = (await q(runtime_factory, wa, "SELECT status, skip_reason FROM inbound_events"))[0]
    assert (ev.status, ev.skip_reason) == ("needs_review", "PROCESSING_FAILED")
    notes = await q(runtime_factory, wa, "SELECT body FROM notifications")
    assert len(notes) == 1 and "process_inbound" in notes[0].body


async def test_email_event_prechecks_and_processing(runtime_factory: Factory, wa: WaTenant) -> None:
    from datetime import UTC, datetime

    from app.contracts.contracts import NormalizedInbound
    from app.jobs import queue

    def inbound(ext: str, **kw: Any) -> NormalizedInbound:
        data: dict[str, Any] = {
            "channel": "email",
            "external_id": ext,
            "from_email": "Buyer@Example.com",
            "text": "Where is my order",
            "received_at": datetime.now(UTC),
        }
        data.update(kw)
        return NormalizedInbound(**data)

    async def add(n: NormalizedInbound) -> None:
        async with session_scope(runtime_factory, tenant_id=wa.tenant_id) as session:
            event_id = (
                await session.execute(
                    text(
                        "INSERT INTO inbound_events (channel, external_id, raw_payload, status)"
                        " VALUES ('email', :e, CAST(:r AS jsonb), 'queued') RETURNING id"
                    ),
                    {
                        "e": n.external_id,
                        "r": __import__("json").dumps({"normalized": n.model_dump(mode="json")}),
                    },
                )
            ).scalar_one()
            await queue.enqueue(
                session, wa.tenant_id, "process_inbound", {"inbound_event_id": str(event_id)}
            )

    await add(inbound("e1"))
    await add(inbound("e2", email_headers={"Precedence": "bulk"}))
    await drain(runtime_factory)
    customers = await q(runtime_factory, wa, "SELECT email_lower FROM customers")
    assert [c.email_lower for c in customers] == ["buyer@example.com"]
    ev = {
        r.external_id: r
        for r in await q(
            runtime_factory, wa, "SELECT external_id, status, skip_reason FROM inbound_events"
        )
    }
    assert ev["e2"].skip_reason == "BULK_PRECEDENCE" and ev["e1"].status == "queued"
    assert len(await q(runtime_factory, wa, "SELECT 1 FROM messages")) == 1
