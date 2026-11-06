"""Section 2.3 - per-tenant traffic rate limiting (highest priority item in the Speed &
Scaling plan): a per-tenant counter on the existing rate_limits table so one noisy tenant's
dashboard API calls or WhatsApp webhook deliveries are briefly held back without affecting
any other tenant."""

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import ratelimit
from app.core.config import get_settings
from app.core.errors import AppError
from tests.auth.helpers import MemoryMailer, active_user, bearer, login
from tests.channels.helpers import setup_wa_tenant, wa_message

Factory = async_sessionmaker[AsyncSession]


async def test_hit_tenant_api_blocks_after_limit_and_buckets_are_separate(
    auth_env: None, runtime_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TENANT_API_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        await ratelimit.hit_tenant_api(runtime_factory, tenant_a)
    with pytest.raises(AppError) as info:
        await ratelimit.hit_tenant_api(runtime_factory, tenant_a)
    assert info.value.code == "E-AUTH-006"
    # a different tenant's bucket is untouched by tenant_a's overage
    await ratelimit.hit_tenant_api(runtime_factory, tenant_b)
    await ratelimit.hit_tenant_api(runtime_factory, tenant_b)


async def test_hit_tenant_api_window_resets(
    auth_env: None, runtime_factory: Factory, monkeypatch: pytest.MonkeyPatch, clock: object
) -> None:
    monkeypatch.setenv("TENANT_API_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()
    tenant_id = uuid.uuid4()
    await ratelimit.hit_tenant_api(runtime_factory, tenant_id)
    await ratelimit.hit_tenant_api(runtime_factory, tenant_id)
    with pytest.raises(AppError):
        await ratelimit.hit_tenant_api(runtime_factory, tenant_id)
    clock.now += timedelta(minutes=1, seconds=1)  # type: ignore[attr-defined]
    await ratelimit.hit_tenant_api(runtime_factory, tenant_id)


async def test_dashboard_api_throttles_one_tenant_without_affecting_another(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_API_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    email_a = await active_user(client, mailer, runtime_factory)
    headers_a = bearer(await login(client, email_a))
    email_b = await active_user(client, mailer, runtime_factory)
    headers_b = bearer(await login(client, email_b))

    statuses = [
        (await client.get("/conversations", headers=headers_a)).status_code for _ in range(4)
    ]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429

    # tenant B is a completely different bucket, so it is unaffected by A's overage.
    response_b = await client.get("/conversations", headers=headers_b)
    assert response_b.status_code == 200


async def test_whatsapp_webhook_throttles_one_tenant_without_affecting_another(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # setup_wa_tenant's own PUT /integrations/gupshup call already spends one hit of
    # tenant_a's budget (it goes through the same authenticated-request limiter), so the
    # limit here accounts for that plus the 2 webhook posts that should still succeed.
    monkeypatch.setenv("TENANT_API_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    tenant_a = await setup_wa_tenant(client, mailer, runtime_factory, monkeypatch)
    tenant_b = await setup_wa_tenant(client, mailer, runtime_factory, monkeypatch)

    first = await client.post(tenant_a.path, json=wa_message("rl-1"))
    second = await client.post(tenant_a.path, json=wa_message("rl-2"))
    third = await client.post(tenant_a.path, json=wa_message("rl-3"))
    assert first.status_code == 200 and second.status_code == 200
    assert third.status_code == 429 and third.json()["code"] == "E-AUTH-006"

    other = await client.post(tenant_b.path, json=wa_message("rl-ok"))
    assert other.status_code == 200 and other.json() == {"status": "queued"}
