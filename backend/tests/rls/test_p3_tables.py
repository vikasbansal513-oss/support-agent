import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope


async def _tenant(factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    tid = uuid.uuid4()
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'T', :s)"),
            {"i": tid, "s": f"t-{tid.hex[:12]}"},
        )
    return tid


async def test_new_tables_have_force_rls(owner_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(owner_factory) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relname = ANY(:n) AND relrowsecurity"
                        " AND relforcerowsecurity"
                    ),
                    {
                        "n": [
                            "integrations",
                            "wa_numbers",
                            "tenant_settings",
                            "external_calls",
                            "eval_runs",
                            "policies",
                        ]
                    },
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 6


async def test_cross_tenant_isolation_and_super_admin_read(
    runtime_factory: async_sessionmaker[AsyncSession],
) -> None:
    a, b = await _tenant(runtime_factory), await _tenant(runtime_factory)
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(text("INSERT INTO integrations (kind) VALUES ('shopify')"))
        await session.execute(
            text("INSERT INTO policies (version, content, content_hash) VALUES (1, '{}', 'h')")
        )
        await session.execute(text("INSERT INTO tenant_settings DEFAULT VALUES"))
    async with session_scope(runtime_factory, tenant_id=b) as session:
        for table in ("integrations", "policies", "tenant_settings"):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0
        with pytest.raises(Exception, match="row-level security"):
            await session.execute(
                text("INSERT INTO integrations (tenant_id, kind) VALUES (:t, 'gmail')"), {"t": a}
            )
    async with session_scope(runtime_factory, super_admin=True) as session:
        seen = (
            await session.execute(
                text("SELECT count(*) FROM integrations WHERE tenant_id = :t"), {"t": a}
            )
        ).scalar_one()
        assert seen == 1
        with pytest.raises(Exception, match="row-level security"):
            await session.execute(
                text("INSERT INTO integrations (tenant_id, kind) VALUES (:t, 'gmail')"), {"t": a}
            )


async def test_wa_numbers_lookup_open_but_writes_tenant_only(
    runtime_factory: async_sessionmaker[AsyncSession],
) -> None:
    a, b = await _tenant(runtime_factory), await _tenant(runtime_factory)
    number = "9199" + str(uuid.uuid4().int)[:8]
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(
            text("INSERT INTO wa_numbers (source_number, webhook_token_hash) VALUES (:s, :h)"),
            {"s": number, "h": "a" * 64},
        )
    async with session_scope(runtime_factory) as session:
        row = (
            await session.execute(
                text("SELECT tenant_id FROM wa_numbers WHERE webhook_token_hash = :h"),
                {"h": "a" * 64},
            )
        ).one()
        assert row.tenant_id == a
    async with session_scope(runtime_factory, tenant_id=b) as session:
        deleted = await session.execute(
            text("DELETE FROM wa_numbers WHERE source_number = :s RETURNING 1"), {"s": number}
        )
        assert deleted.first() is None
        with pytest.raises(Exception, match="row-level security"):
            await session.execute(
                text(
                    "INSERT INTO wa_numbers (source_number, webhook_token_hash, tenant_id)"
                    " VALUES ('9188000000001', :h, :t)"
                ),
                {"h": "b" * 64, "t": a},
            )


async def test_eval_runs_scoped(runtime_factory: async_sessionmaker[AsyncSession]) -> None:
    a, b = await _tenant(runtime_factory), await _tenant(runtime_factory)
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(
            text(
                "INSERT INTO eval_runs (kind, tenant_id, metrics, passed)"
                " VALUES ('policy_regression', :t, '{}', true)"
            ),
            {"t": a},
        )
    async with session_scope(runtime_factory, tenant_id=b) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM eval_runs WHERE tenant_id = :t"), {"t": a}
            )
        ).scalar_one()
        assert count == 0
