import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from tests.rls.test_p4_tables import seed, tenant

Factory = async_sessionmaker[AsyncSession]
TABLES = [
    "agent_runs",
    "agent_actions",
    "handoffs",
    "llm_calls",
    "answer_reports",
    "shadow_verdicts",
]


async def seed_agent(factory: Factory, tid: uuid.UUID) -> None:
    await seed(factory, tid)
    async with session_scope(factory, tenant_id=tid) as session:
        ids = (await session.execute(text("SELECT id FROM conversations"))).scalar_one()
        event = (await session.execute(text("SELECT id FROM inbound_events"))).scalar_one()
        run = (
            await session.execute(
                text(
                    "INSERT INTO agent_runs (inbound_event_id, conversation_id, graph_version, mode)"
                    " VALUES (:e, :c, 'v', 'shadow') RETURNING id"
                ),
                {"e": event, "c": ids},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO agent_actions (run_id, conversation_id, action_type, idempotency_key,"
                " status, recipient, body_text) VALUES (:r, :c, 'reply_customer', 'k', 'shadow', '+91', 'b')"
            ),
            {"r": run, "c": ids},
        )
        await session.execute(
            text(
                "INSERT INTO handoffs (conversation_id, run_id, reason_code, summary)"
                " VALUES (:c, :r, 'ANGRY', '{}')"
            ),
            {"c": ids, "r": run},
        )
        await session.execute(
            text(
                "INSERT INTO llm_calls (purpose, provider, model, latency_ms, outcome)"
                " VALUES ('classify', 'fake', 'm', 5, 'ok')"
            )
        )


async def test_force_rls_on_p5_tables(owner_factory: Factory) -> None:
    async with session_scope(owner_factory) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relname = ANY(:n) AND relrowsecurity"
                        " AND relforcerowsecurity"
                    ),
                    {"n": TABLES},
                )
            )
            .scalars()
            .all()
        )
    assert sorted(rows) == sorted(TABLES)


async def test_tenant_isolation_and_super_admin_read(runtime_factory: Factory) -> None:
    a, b = await tenant(runtime_factory), await tenant(runtime_factory)
    await seed_agent(runtime_factory, a)
    async with session_scope(runtime_factory, tenant_id=b) as session:
        for table in TABLES:
            assert (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
    async with session_scope(runtime_factory) as session:
        for table in TABLES:
            assert (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
    async with session_scope(runtime_factory, super_admin=True) as session:
        for table in ("agent_runs", "handoffs", "llm_calls"):
            count = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": a}
                )
            ).scalar_one()
            assert count == 1, table
        assert (
            await session.execute(
                text("SELECT count(*) FROM agent_actions WHERE tenant_id = :t"), {"t": a}
            )
        ).scalar_one() == 0


async def test_cross_tenant_write_and_second_open_handoff_rejected(
    runtime_factory: Factory,
) -> None:
    a, b = await tenant(runtime_factory), await tenant(runtime_factory)
    await seed_agent(runtime_factory, a)
    with pytest.raises(Exception, match="row-level security|violates foreign key"):
        async with session_scope(runtime_factory, tenant_id=b) as session:
            conv = (await session.execute(text("SELECT id FROM conversations"), {})).first()
            await session.execute(
                text(
                    "INSERT INTO llm_calls (tenant_id, purpose, provider, model, latency_ms, outcome)"
                    " VALUES (:t, 'x', 'f', 'm', 1, 'ok')"
                ),
                {"t": a},
            )
            assert conv is None
    with pytest.raises(Exception, match="ux_handoff_one_open"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            conv_id = (await session.execute(text("SELECT id FROM conversations"))).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO handoffs (conversation_id, reason_code, summary)"
                    " VALUES (:c, 'LEGAL', '{}')"
                ),
                {"c": conv_id},
            )


async def test_prices_table_is_global_read_only_for_runtime(runtime_factory: Factory) -> None:
    async with session_scope(runtime_factory) as session:
        assert (
            await session.execute(text("SELECT count(*) FROM llm_model_prices"))
        ).scalar_one() >= 0
    with pytest.raises(Exception, match="permission denied"):
        async with session_scope(runtime_factory) as session:
            await session.execute(text("INSERT INTO llm_model_prices VALUES ('p', 'm', 1, 1)"))
