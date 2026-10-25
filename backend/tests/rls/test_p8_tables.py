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
            {"i": tid, "s": f"r8-{tid.hex[:12]}"},
        )
    return tid


async def test_force_rls_and_isolation(runtime_factory: Factory, owner_factory: Factory) -> None:
    a, b = await tenant(runtime_factory), await tenant(runtime_factory)
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(
            text(
                "INSERT INTO privacy_requests (subject_ref, kind) VALUES ('+919800000000', 'export')"
            )
        )
        await session.execute(
            text(
                "INSERT INTO consents (subject_type, subject_id, purpose, notice_version, granted, source)"
                " VALUES ('customer', gen_random_uuid(), 'ai_processing', 'v', true, 's')"
            )
        )
    async with session_scope(owner_factory) as session:
        flags = (
            await session.execute(
                text(
                    "SELECT relname FROM pg_class WHERE relname IN ('consents','privacy_requests')"
                    " AND relrowsecurity AND relforcerowsecurity"
                )
            )
        ).all()
    assert len(flags) == 2
    async with session_scope(runtime_factory, tenant_id=b) as session:
        for table in ("consents", "privacy_requests"):
            assert (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0  # noqa: S608
    async with session_scope(runtime_factory, tenant_id=b) as session:
        with pytest.raises(Exception, match="row-level security"):
            await session.execute(
                text(
                    "INSERT INTO privacy_requests (tenant_id, subject_ref, kind) VALUES (:t, 'x@y.com', 'delete')"
                ),
                {"t": a},
            )


async def test_one_open_request_per_subject_and_kind(runtime_factory: Factory) -> None:
    a = await tenant(runtime_factory)
    sql = text(
        "INSERT INTO privacy_requests (subject_ref, kind) VALUES ('+919800000000', 'export')"
    )
    async with session_scope(runtime_factory, tenant_id=a) as session:
        await session.execute(sql)
    with pytest.raises(Exception, match="ux_privacy_one_open"):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(sql)


async def test_platform_flags_default_off(runtime_factory: Factory) -> None:
    async with session_scope(runtime_factory) as session:
        value = (
            await session.execute(
                text("SELECT enabled FROM platform_flags WHERE name='global_pause'")
            )
        ).scalar_one()
    assert value is False
