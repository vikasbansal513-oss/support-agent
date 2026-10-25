import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope

Factory = async_sessionmaker[AsyncSession]
TABLES = ["conversation_state", "action_usage"]


async def tenant(factory: Factory) -> uuid.UUID:
    tid = uuid.uuid4()
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'T', :s)"),
            {"i": tid, "s": f"r9-{tid.hex[:12]}"},
        )
    return tid


async def seed(factory: Factory, tid: uuid.UUID) -> uuid.UUID:
    async with session_scope(factory, tenant_id=tid) as session:
        customer = (
            await session.execute(
                text("INSERT INTO customers (phone_e164) VALUES ('+919800000001') RETURNING id")
            )
        ).scalar_one()
        conv = (
            await session.execute(
                text(
                    "INSERT INTO conversations (customer_id, channel) VALUES (:c, 'whatsapp')"
                    " RETURNING id"
                ),
                {"c": customer},
            )
        ).scalar_one()
        await session.execute(
            text("INSERT INTO conversation_state (conversation_id) VALUES (:c)"), {"c": conv}
        )
        await session.execute(
            text(
                "INSERT INTO action_usage (customer_id, day, tool, count, sum_paise)"
                " VALUES (:c, '2026-01-01', 'refund_create', 1, 100)"
            ),
            {"c": customer},
        )
    return uuid.UUID(str(customer))


async def test_new_tables_have_forced_rls(owner_factory: Factory) -> None:
    async with session_scope(owner_factory) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relname = ANY(:n)"
                        " AND relrowsecurity AND relforcerowsecurity"
                    ),
                    {"n": TABLES},
                )
            )
            .scalars()
            .all()
        )
    assert sorted(rows) == sorted(TABLES)


async def test_isolation_between_tenants(runtime_factory: Factory) -> None:
    a, b = await tenant(runtime_factory), await tenant(runtime_factory)
    await seed(runtime_factory, a)
    async with session_scope(runtime_factory, tenant_id=b) as session:
        for table in TABLES:
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table
    async with session_scope(runtime_factory) as session:
        for table in TABLES:
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table


async def test_cross_tenant_composite_fk(runtime_factory: Factory) -> None:
    a, b = await tenant(runtime_factory), await tenant(runtime_factory)
    customer = await seed(runtime_factory, a)
    with pytest.raises(Exception, match="row-level security|violates foreign key"):
        async with session_scope(runtime_factory, tenant_id=b) as session:
            await session.execute(
                text(
                    "INSERT INTO action_usage (customer_id, day, tool) VALUES (:c, '2026-01-01',"
                    " 'order_cancel')"
                ),
                {"c": customer},
            )


async def test_usage_tool_and_state_awaiting_checks(runtime_factory: Factory) -> None:
    a = await tenant(runtime_factory)
    customer = await seed(runtime_factory, a)
    with pytest.raises(Exception, match="check constraint"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(
                text(
                    "INSERT INTO action_usage (customer_id, day, tool) VALUES (:c, '2026-01-02',"
                    " 'format_disk')"
                ),
                {"c": customer},
            )
    with pytest.raises(Exception, match="check constraint"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(text("UPDATE conversation_state SET awaiting = 'maybe'"))


async def test_one_active_write_per_conversation(runtime_factory: Factory) -> None:
    a = await tenant(runtime_factory)
    await seed(runtime_factory, a)
    insert = text(
        "INSERT INTO agent_actions (conversation_id, action_type, idempotency_key, status,"
        " tool_name, risk_tier) SELECT id, 'tool_call', :k, 'awaiting_customer', 'order_cancel',"
        " 'T2' FROM conversations"
    )
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(insert, {"k": "k1"})
    with pytest.raises(Exception, match="ux_actions_one_active_write"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(insert, {"k": "k2"})
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(
            text("UPDATE agent_actions SET status = 'confirmed' WHERE idempotency_key = 'k1'")
        )
        await session.execute(insert, {"k": "k2"})


async def test_new_columns_and_statuses(runtime_factory: Factory) -> None:
    a = await tenant(runtime_factory)
    await seed(runtime_factory, a)
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(
            text(
                "INSERT INTO agent_actions (conversation_id, action_type, idempotency_key, status,"
                " tool_name, risk_tier, amount_paise, requires_approval, order_ref)"
                " SELECT id, 'tool_call', 'k9', 'pending_approval', 'refund_create', 'T3', 500,"
                " true, '#1' FROM conversations"
            )
        )
    with pytest.raises(Exception, match="check constraint"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(text("UPDATE agent_actions SET status = 'teleported'"))
    with pytest.raises(Exception, match="check constraint"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(text("UPDATE agent_actions SET amount_paise = -5"))


async def test_tenant_kill_and_run_plan_columns(
    runtime_factory: Factory, owner_factory: Factory
) -> None:
    a = await tenant(runtime_factory)
    async with session_scope(owner_factory, super_admin=True) as session:
        row = (
            await session.execute(
                text("SELECT tool_kill, auto_enabled_by FROM tenants WHERE id = :t"), {"t": a}
            )
        ).one()
        cols = (
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'agent_runs' AND column_name IN ('plan','reply_attempts')"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert row.tool_kill == {} and row.auto_enabled_by is None
    assert sorted(cols) == ["plan", "reply_attempts"]
