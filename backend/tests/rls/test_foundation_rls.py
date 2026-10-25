import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import audit, tenancy
from app.core.audit import AuditEvent
from app.core.db import check_rls_safety, make_engine, session_scope

Factory = async_sessionmaker[AsyncSession]
TENANT_A = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
TENANT_B = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")


async def _count(factory: Factory, table: str, **scope: object) -> int:
    async with session_scope(factory, **scope) as session:  # type: ignore[arg-type]
        return int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


async def test_runtime_role_is_safe_and_clean_schema_passes(
    runtime_factory: Factory, probe_table: str
) -> None:
    async with session_scope(runtime_factory) as session:
        assert await check_rls_safety(session) == []


async def test_no_context_sees_nothing_and_cannot_insert(
    runtime_factory: Factory, probe_table: str
) -> None:
    async with session_scope(runtime_factory, tenant_id=TENANT_A) as session:
        await session.execute(text(f"INSERT INTO {probe_table} (note) VALUES ('a')"))
    assert await _count(runtime_factory, probe_table) == 0
    with pytest.raises(DBAPIError):
        async with session_scope(runtime_factory) as session:
            await session.execute(text(f"INSERT INTO {probe_table} (note) VALUES ('x')"))


async def test_cross_tenant_read_write_blocked(runtime_factory: Factory, probe_table: str) -> None:
    async with session_scope(runtime_factory, tenant_id=TENANT_A) as session:
        await session.execute(text(f"INSERT INTO {probe_table} (note) VALUES ('a')"))
    assert await _count(runtime_factory, probe_table, tenant_id=TENANT_B) == 0
    assert await _count(runtime_factory, probe_table, tenant_id=TENANT_A) == 1
    async with session_scope(runtime_factory, tenant_id=TENANT_B) as session:
        updated = await session.execute(text(f"UPDATE {probe_table} SET note = 'hack'"))
        deleted = await session.execute(text(f"DELETE FROM {probe_table}"))
        assert updated.rowcount == 0 and deleted.rowcount == 0  # type: ignore[attr-defined]
    with pytest.raises(DBAPIError):
        async with session_scope(runtime_factory, tenant_id=TENANT_B) as session:
            await session.execute(
                text(f"INSERT INTO {probe_table} (tenant_id, note) VALUES (:t, 'forged')"),
                {"t": TENANT_A},
            )


async def test_super_admin_read_only_policy(runtime_factory: Factory, probe_table: str) -> None:
    async with session_scope(runtime_factory, tenant_id=TENANT_A) as session:
        await session.execute(text(f"INSERT INTO {probe_table} (note) VALUES ('a')"))
    assert await _count(runtime_factory, probe_table, super_admin=True) == 1
    async with session_scope(runtime_factory, super_admin=True) as session:
        result = await session.execute(text(f"DELETE FROM {probe_table}"))
        assert result.rowcount == 0  # type: ignore[attr-defined]


async def test_context_does_not_leak_across_pooled_connections(
    test_db: object, probe_table: str
) -> None:
    from tests.conftest import TestDb

    assert isinstance(test_db, TestDb)
    engine = make_engine(test_db.app_url, pool_size=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_scope(factory, tenant_id=TENANT_A, super_admin=True) as session:
            await session.execute(text(f"INSERT INTO {probe_table} (note) VALUES ('a')"))
        async with session_scope(factory) as session:
            leaked = (
                await session.execute(
                    text(
                        "SELECT current_setting('app.tenant_id', true), "
                        "current_setting('app.is_super_admin', true)"
                    )
                )
            ).one()
            assert leaked[0] in ("", None) or leaked[0] == ""
            assert leaked[1] in ("off", "")
            count = (
                await session.execute(text(f"SELECT count(*) FROM {probe_table}"))
            ).scalar_one()
            assert count == 0
        async with factory() as session, session.begin():
            raw = (
                await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            ).scalar_one()
            assert raw in ("", None)
    finally:
        await engine.dispose()


async def test_context_vars_set_inside_scope_and_reset(runtime_factory: Factory) -> None:
    async with session_scope(runtime_factory, tenant_id=TENANT_A, user_id=TENANT_B):
        assert tenancy.current_tenant.get() == TENANT_A
        assert tenancy.current_user.get() == TENANT_B
    assert tenancy.current_tenant.get() is None
    assert tenancy.current_super_admin.get() is False


async def test_audit_log_is_append_only_and_tenant_scoped(runtime_factory: Factory) -> None:
    async with session_scope(runtime_factory, tenant_id=TENANT_A) as session:
        await audit.record(
            session,
            AuditEvent(
                action="test.audit",
                entity_type="x",
                new_value={"password": "p", "phone": "9811100001", "ok": 1},
                reason="called 9811100001",
            ),
        )
    async with session_scope(runtime_factory, tenant_id=TENANT_A) as session:
        row = (
            await session.execute(text("SELECT new_value, reason, tenant_id FROM audit_log"))
        ).one()
        assert row[0] == {"password": "[REDACTED]", "phone": "[REDACTED]", "ok": 1}
        assert row[1] == "called [PHONE]"
        assert row[2] == TENANT_A
    for statement in (
        "UPDATE audit_log SET action = 'tampered'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        with pytest.raises(DBAPIError):
            async with session_scope(runtime_factory, tenant_id=TENANT_A) as session:
                await session.execute(text(statement))
    assert await _count(runtime_factory, "audit_log", tenant_id=TENANT_B) == 0
    with pytest.raises(DBAPIError):
        async with session_scope(runtime_factory, tenant_id=TENANT_B) as session:
            await audit.record(session, AuditEvent(action="forged"), tenant_id=TENANT_A)
    async with session_scope(runtime_factory) as session:
        await audit.record(session, AuditEvent(action="system.event"))
    assert await _count(runtime_factory, "audit_log", tenant_id=TENANT_A) == 1
    async with session_scope(runtime_factory, super_admin=True) as session:
        null_rows = (
            await session.execute(text("SELECT count(*) FROM audit_log WHERE tenant_id IS NULL"))
        ).scalar_one()
        assert null_rows >= 1


async def test_audit_super_admin_reads_all(runtime_factory: Factory) -> None:
    async with session_scope(runtime_factory, tenant_id=TENANT_B) as session:
        await audit.record(session, AuditEvent(action="b.event"))
    assert await _count(runtime_factory, "audit_log", super_admin=True) >= 2


async def test_new_tenant_table_without_rls_is_flagged(
    runtime_factory: Factory, owner_factory: Factory
) -> None:
    name = f"leaky_{uuid.uuid4().hex[:8]}"
    async with owner_factory() as session, session.begin():
        await session.execute(text(f"CREATE TABLE {name} (id int, tenant_id uuid)"))
    try:
        async with session_scope(runtime_factory) as session:
            problems = await check_rls_safety(session)
        assert any(name in p and "RLS" in p for p in problems)
    finally:
        async with owner_factory() as session, session.begin():
            await session.execute(text(f"DROP TABLE {name}"))


async def test_unlisted_global_table_is_flagged(
    runtime_factory: Factory, owner_factory: Factory
) -> None:
    name = f"global_{uuid.uuid4().hex[:8]}"
    async with owner_factory() as session, session.begin():
        await session.execute(text(f"CREATE TABLE {name} (id int)"))
    try:
        async with session_scope(runtime_factory) as session:
            problems = await check_rls_safety(session)
        assert any(name in p and "allowlist" in p for p in problems)
    finally:
        async with owner_factory() as session, session.begin():
            await session.execute(text(f"DROP TABLE {name}"))


async def test_owner_role_is_flagged_for_owning_tables(
    owner_factory: Factory, probe_table: str
) -> None:
    async with session_scope(owner_factory) as session:
        problems = await check_rls_safety(session)
    assert any("owns tables" in p for p in problems)
