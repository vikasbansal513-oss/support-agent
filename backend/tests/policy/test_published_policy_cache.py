"""Section 2 - "Redis caching for autonomy/policy settings". The actual store is an
in-process TTL cache rather than Redis (see app/policy/service.py::get_published_policy
docstring and the decision recorded with the user): zero new infra, same functional
contract - a tenant's write-gate/reply-language/handoff lookups stop re-querying+
re-validating the published policy on every single message, and a tenant's own publish()
invalidates its own cache entry immediately (already covered end-to-end by
tests/policy/test_api.py's publish-then-/agent/status assertion). These tests cover the
cache mechanics themselves: a stale read within the TTL, and per-tenant isolation."""

import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from app.policy import service as policy_service
from tests.auth.helpers import MemoryMailer, active_user, bearer, login, set_tenant_status
from tests.policy.test_v2_api import v2

Factory = async_sessionmaker[AsyncSession]


async def _owner(
    client: httpx.AsyncClient, mailer: MemoryMailer, factory: Factory
) -> tuple[str, dict[str, str]]:
    email = await active_user(client, mailer, factory)
    return email, bearer(await login(client, email))


async def _publish(client: httpx.AsyncClient, headers: dict[str, str]) -> int:
    created = (await client.post("/policies", json={"content": v2()}, headers=headers)).json()
    published = await client.post(f"/policies/{created['id']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return int(published.json()["version"])


async def _bump_published_version_directly(factory: Factory, tenant_id: uuid.UUID) -> int:
    """Change the already-published row's version number by raw SQL, bypassing
    policy_service.publish (and therefore its cache invalidation), to prove the cache
    holds the stale value until something actually invalidates it."""
    async with session_scope(factory, tenant_id=tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "UPDATE policies SET version = version + 100 WHERE status = 'published'"
                    " RETURNING version"
                )
            )
        ).one()
    return int(row.version)


async def test_cache_hides_a_direct_db_change_until_invalidated(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    policy_service.invalidate_published_policy_cache()
    email, headers = await _owner(client, mailer, runtime_factory)
    tenant_id = await set_tenant_status(runtime_factory, email, "active")
    v1 = await _publish(client, headers)

    async with session_scope(runtime_factory, tenant_id=tenant_id) as session:
        _, version = await policy_service.get_published_policy(session)
    assert version == v1

    bumped = await _bump_published_version_directly(runtime_factory, tenant_id)
    assert bumped != v1
    async with session_scope(runtime_factory, tenant_id=tenant_id) as session:
        _, still_cached = await policy_service.get_published_policy(session)
    assert still_cached == v1  # stale on purpose - nothing has invalidated it yet

    policy_service.invalidate_published_policy_cache(tenant_id)
    async with session_scope(runtime_factory, tenant_id=tenant_id) as session:
        _, fresh = await policy_service.get_published_policy(session)
    assert fresh == bumped


async def test_cache_is_isolated_per_tenant(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    policy_service.invalidate_published_policy_cache()
    email_a, headers_a = await _owner(client, mailer, runtime_factory)
    email_b, headers_b = await _owner(client, mailer, runtime_factory)
    tenant_a = await set_tenant_status(runtime_factory, email_a, "active")
    tenant_b = await set_tenant_status(runtime_factory, email_b, "active")
    version_a = await _publish(client, headers_a)
    version_b = await _publish(client, headers_b)

    async with session_scope(runtime_factory, tenant_id=tenant_a) as session:
        _, got_a = await policy_service.get_published_policy(session)
    async with session_scope(runtime_factory, tenant_id=tenant_b) as session:
        _, got_b = await policy_service.get_published_policy(session)
    assert got_a == version_a and got_b == version_b

    # Invalidating tenant_a's entry must not touch tenant_b's cached value.
    bumped_a = await _bump_published_version_directly(runtime_factory, tenant_a)
    policy_service.invalidate_published_policy_cache(tenant_a)
    async with session_scope(runtime_factory, tenant_id=tenant_a) as session:
        _, refreshed_a = await policy_service.get_published_policy(session)
    async with session_scope(runtime_factory, tenant_id=tenant_b) as session:
        _, still_b = await policy_service.get_published_policy(session)
    assert refreshed_a == bumped_a
    assert still_b == version_b
