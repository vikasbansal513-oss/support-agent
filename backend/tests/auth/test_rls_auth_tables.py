import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import check_rls_safety, session_scope
from tests.auth.helpers import new_email

Factory = async_sessionmaker[AsyncSession]


async def _tenant_with_member(factory: Factory) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'T', :s)"),
            {"i": tenant_id, "s": f"t-{tenant_id.hex[:10]}"},
        )
        await session.execute(
            text("INSERT INTO users (id, email, name) VALUES (:i, :e, 'U')"),
            {"i": user_id, "e": new_email()},
        )
        await session.execute(
            text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'owner')"),
            {"t": tenant_id, "u": user_id},
        )
    return tenant_id, user_id


async def test_tenant_sees_only_itself_and_super_admin_sees_all(
    auth_env: None, runtime_factory: Factory
) -> None:
    a, _ = await _tenant_with_member(runtime_factory)
    b, _ = await _tenant_with_member(runtime_factory)
    async with session_scope(runtime_factory, tenant_id=a) as session:
        ids = (await session.execute(text("SELECT id FROM tenants"))).scalars().all()
        assert ids == [a]
        none = await session.execute(text("UPDATE tenants SET name = 'x' WHERE id = :b"), {"b": b})
        assert none.rowcount == 0  # type: ignore[attr-defined]
    async with session_scope(runtime_factory) as session:
        assert (await session.execute(text("SELECT count(*) FROM tenants"))).scalar_one() == 0
    async with session_scope(runtime_factory, super_admin=True) as session:
        assert (await session.execute(text("SELECT count(*) FROM tenants"))).scalar_one() >= 2


async def test_tenant_cannot_create_tenants_or_foreign_memberships(
    auth_env: None, runtime_factory: Factory
) -> None:
    a, _ = await _tenant_with_member(runtime_factory)
    b, user_b = await _tenant_with_member(runtime_factory)
    with pytest.raises(DBAPIError):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'Evil', 'evil-slug')"),
                {"i": uuid.uuid4()},
            )
    new_user = uuid.uuid4()
    async with session_scope(runtime_factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO users (id, email, name) VALUES (:i, :e, 'N')"),
            {"i": new_user, "e": new_email()},
        )
    with pytest.raises(DBAPIError):
        async with session_scope(runtime_factory, tenant_id=a) as session:
            await session.execute(
                text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'agent')"),
                {"t": b, "u": new_user},
            )


async def test_membership_visible_to_own_tenant_and_own_user_only(
    auth_env: None, runtime_factory: Factory
) -> None:
    a, user_a = await _tenant_with_member(runtime_factory)
    b, user_b = await _tenant_with_member(runtime_factory)
    async with session_scope(runtime_factory, tenant_id=a) as session:
        rows = (await session.execute(text("SELECT user_id FROM memberships"))).scalars().all()
        assert rows == [user_a]
    async with session_scope(runtime_factory, user_id=user_b) as session:
        rows = (await session.execute(text("SELECT tenant_id FROM memberships"))).scalars().all()
        assert rows == [b]
    async with session_scope(runtime_factory) as session:
        assert (await session.execute(text("SELECT count(*) FROM memberships"))).scalar_one() == 0


async def test_one_user_one_brand_and_slug_checks(auth_env: None, runtime_factory: Factory) -> None:
    _, user = await _tenant_with_member(runtime_factory)
    other = uuid.uuid4()
    with pytest.raises(DBAPIError):
        async with session_scope(runtime_factory, super_admin=True) as session:
            await session.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'O', 'other-slug-1')"),
                {"i": other},
            )
            await session.execute(
                text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'owner')"),
                {"t": other, "u": user},
            )
    for bad_slug in ("A", "UPPER-case", "-lead", "x" * 60):
        with pytest.raises(DBAPIError):
            async with session_scope(runtime_factory, super_admin=True) as session:
                await session.execute(
                    text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'B', :s)"),
                    {"i": uuid.uuid4(), "s": bad_slug},
                )


async def test_email_must_be_lowercase_and_unique(auth_env: None, runtime_factory: Factory) -> None:
    for bad in ("Mixed@Example.com", "x" * 260 + "@example.com"):
        with pytest.raises(DBAPIError):
            async with session_scope(runtime_factory, super_admin=True) as session:
                await session.execute(
                    text("INSERT INTO users (email, name) VALUES (:e, 'U')"), {"e": bad}
                )
    email = new_email()
    async with session_scope(runtime_factory, super_admin=True) as session:
        await session.execute(
            text("INSERT INTO users (email, name) VALUES (:e, 'U')"), {"e": email}
        )
    with pytest.raises(DBAPIError):
        async with session_scope(runtime_factory, super_admin=True) as session:
            await session.execute(
                text("INSERT INTO users (email, name) VALUES (:e, 'U')"), {"e": email}
            )


async def test_schema_still_passes_rls_safety_with_auth_tables(
    auth_env: None, runtime_factory: Factory
) -> None:
    async with session_scope(runtime_factory) as session:
        assert await check_rls_safety(session) == []
