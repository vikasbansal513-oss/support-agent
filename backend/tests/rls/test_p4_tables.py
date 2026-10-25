import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope

Factory = async_sessionmaker[AsyncSession]


async def tenant(factory: Factory) -> uuid.UUID:
    tid = uuid.uuid4()
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'T', :s)"),
            {"i": tid, "s": f"r4-{tid.hex[:12]}"},
        )
    return tid


async def seed(factory: Factory, tid: uuid.UUID) -> uuid.UUID:
    async with session_scope(factory, tenant_id=tid) as session:
        customer = (
            await session.execute(
                text("INSERT INTO customers (phone_e164) VALUES ('+919800000000') RETURNING id")
            )
        ).scalar_one()
        conv = (
            await session.execute(
                text(
                    "INSERT INTO conversations (customer_id, channel) VALUES (:c, 'whatsapp') RETURNING id"
                ),
                {"c": customer},
            )
        ).scalar_one()
        event = (
            await session.execute(
                text(
                    "INSERT INTO inbound_events (channel, external_id, raw_payload) VALUES ('whatsapp', 'e1', '{}') RETURNING id"
                )
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO messages (conversation_id, direction, sender, channel, content, inbound_event_id) VALUES (:c, 'in', 'customer', 'whatsapp', 'hi', :e)"
            ),
            {"c": conv, "e": event},
        )
        await session.execute(
            text(
                "INSERT INTO usage_counters (period_start, conversation_count) VALUES ('2026-01-01', 1)"
            )
        )
        await session.execute(text("INSERT INTO jobs (kind) VALUES ('x')"))
        await session.execute(text("INSERT INTO notifications (title) VALUES ('n')"))
        await session.execute(
            text(
                "INSERT INTO opt_outs (customer_id, channel, source) VALUES (:c, 'whatsapp', 's')"
            ),
            {"c": customer},
        )
    return uuid.UUID(str(customer))


TABLES = [
    "customers",
    "conversations",
    "inbound_events",
    "messages",
    "usage_counters",
    "jobs",
    "notifications",
    "opt_outs",
]


async def test_force_rls_on_p4_tables(owner_factory: Factory) -> None:
    async with session_scope(owner_factory) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relname = ANY(:n) AND relrowsecurity AND relforcerowsecurity"
                    ),
                    {"n": TABLES},
                )
            )
            .scalars()
            .all()
        )
    assert sorted(rows) == sorted(TABLES)


async def test_tenant_isolation_all_p4_tables(runtime_factory: Factory) -> None:
    a, b = await tenant(runtime_factory), await tenant(runtime_factory)
    await seed(runtime_factory, a)
    async with session_scope(runtime_factory, tenant_id=b) as session:
        for table in TABLES:
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, table
    async with session_scope(runtime_factory) as session:
        for table in TABLES:
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, table


async def test_cross_tenant_writes_rejected(runtime_factory: Factory) -> None:
    a, b = await tenant(runtime_factory), await tenant(runtime_factory)
    customer = await seed(runtime_factory, a)
    with pytest.raises(Exception, match="row-level security|violates foreign key"):
        async with session_scope(runtime_factory, tenant_id=b) as session:
            await session.execute(
                text("INSERT INTO conversations (customer_id, channel) VALUES (:c, 'email')"),
                {"c": customer},
            )
    with pytest.raises(Exception, match="row-level security"):
        async with session_scope(runtime_factory, tenant_id=b) as session:
            await session.execute(
                text("INSERT INTO jobs (tenant_id, kind) VALUES (:t, 'x')"), {"t": a}
            )


async def test_super_admin_reads_selected_and_writes_jobs(runtime_factory: Factory) -> None:
    a = await tenant(runtime_factory)
    await seed(runtime_factory, a)
    async with session_scope(runtime_factory, super_admin=True) as session:
        for table in ("conversations", "usage_counters", "jobs", "notifications"):
            count = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": a}
                )
            ).scalar_one()
            assert count == 1, table
        for table in ("customers", "messages", "inbound_events", "opt_outs"):
            count = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": a}
                )
            ).scalar_one()
            assert count == 0, table
        await session.execute(text("INSERT INTO jobs (tenant_id, kind) VALUES (:t, 'y')"), {"t": a})


async def test_unique_constraints(runtime_factory: Factory) -> None:
    a = await tenant(runtime_factory)
    customer = await seed(runtime_factory, a)
    with pytest.raises(Exception, match="ux_conv_one_open"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(
                text("INSERT INTO conversations (customer_id, channel) VALUES (:c, 'whatsapp')"),
                {"c": customer},
            )
    with pytest.raises(Exception, match="inbound_events_tenant_id_channel_external_id_key"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(
                text(
                    "INSERT INTO inbound_events (channel, external_id, raw_payload) VALUES ('whatsapp', 'e1', '{}')"
                )
            )
    with pytest.raises(Exception, match="ux_customers_phone"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(
                text("INSERT INTO customers (phone_e164) VALUES ('+919800000000')")
            )
    with pytest.raises(Exception, match="customers_check|violates check"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(text("INSERT INTO customers (phone_e164) VALUES ('98765')"))
    with pytest.raises(Exception, match="customers_check|violates check"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(text("INSERT INTO customers (created_at) VALUES (now())"))
