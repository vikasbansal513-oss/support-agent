import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_session_factory, session_scope
from app.integrations.service import get_transport
from tests.auth.helpers import MemoryMailer, active_user, bearer, error_code, login

Factory = async_sessionmaker[AsyncSession]
SHOP = {"shop_domain": "brand-one.myshopify.com", "access_token": "shpat_secret_abcd1234"}


def gupshup_body() -> dict[str, str]:
    number = "9198" + str(uuid.uuid4().int)[:8]
    return {"userid": "gs-user", "password": "gs-pass-wxyz", "source_number": number}


class Upstream:
    def __init__(self) -> None:
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        if "shopify" in request.url.host:
            return httpx.Response(self.status, json={"shop": {}})
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(self.status, json={"token": "t"})
        return httpx.Response(self.status, json={})


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
async def api(client: httpx.AsyncClient, upstream: Upstream) -> AsyncIterator[httpx.AsyncClient]:
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_transport] = lambda: httpx.MockTransport(upstream.handler)
    yield client


async def owner(
    api: httpx.AsyncClient, mailer: MemoryMailer, factory: Factory
) -> tuple[dict[str, str], str]:
    email = await active_user(api, mailer, factory)
    return bearer(await login(api, email)), email


async def test_list_empty_then_put_shopify_masks_secret(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    listed = (await api.get("/integrations", headers=headers)).json()
    assert [i["kind"] for i in listed] == ["shopify", "gupshup", "gmail", "shiprocket", "delhivery"]
    assert all(not i["configured"] and i["status"] == "not_configured" for i in listed)
    put = await api.put("/integrations/shopify", json=SHOP, headers=headers)
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["configured"] and body["secret_hint"] == "Set hai (...1234)"
    assert body["config"] == {"shop_domain": "brand-one.myshopify.com"}
    assert "shpat_secret" not in put.text
    again = (await api.get("/integrations", headers=headers)).text
    assert "shpat_secret" not in again


async def test_secret_encrypted_in_db(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    await api.put("/integrations/shopify", json=SHOP, headers=headers)
    async with session_scope(runtime_factory, super_admin=True) as session:
        enc = (await session.execute(text("SELECT secret_enc FROM integrations"))).scalars().all()
    assert enc and all("shpat_secret" not in e for e in enc)


async def test_validation_and_unknown_kind(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    bad = await api.put(
        "/integrations/shopify", json={**SHOP, "shop_domain": "evil.com"}, headers=headers
    )
    assert bad.status_code == 422 and error_code(bad) == "E-VAL-001"
    assert (
        await api.put("/integrations/gmail", json={"credential": "abcdefgh"}, headers=headers)
    ).status_code == 404
    short = await api.put(
        "/integrations/shiprocket", json={"credential": "nocolonhere"}, headers=headers
    )
    assert short.status_code == 422
    ok = await api.put(
        "/integrations/delhivery", json={"credential": "tokentoken12"}, headers=headers
    )
    assert ok.status_code == 200 and ok.json()["secret_hint"] == "Set hai (...n12)".replace(
        "n12", "en12"
    )
    sr = await api.put(
        "/integrations/shiprocket", json={"credential": "me@example.com:hunter22"}, headers=headers
    )
    assert sr.json()["config"] == {"login": "me@example.com"} and "hunter22" not in sr.text


async def test_requires_auth(api: httpx.AsyncClient) -> None:
    assert (await api.get("/integrations")).status_code == 401
    assert (await api.put("/integrations/shopify", json=SHOP)).status_code == 401


async def test_gupshup_webhook_url_once_and_rotation(
    api: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://api.example.com/")
    get_settings.cache_clear()
    headers, _ = await owner(api, mailer, runtime_factory)
    GUPSHUP = gupshup_body()
    first = (await api.put("/integrations/gupshup", json=GUPSHUP, headers=headers)).json()
    url = first["webhook_url"]
    assert url.startswith("https://api.example.com/webhooks/whatsapp/")
    token = url.rsplit("/", 1)[1]
    async with session_scope(runtime_factory, super_admin=True) as session:
        stored = (
            (await session.execute(text("SELECT webhook_token_hash FROM wa_numbers")))
            .scalars()
            .all()
        )
    from app.auth.tokens import sha256_hex

    assert sha256_hex(token) in stored and token not in json.dumps(stored)
    second = (await api.put("/integrations/gupshup", json=GUPSHUP, headers=headers)).json()
    assert second["webhook_url"] is None
    assert "gs-pass" not in json.dumps(second)
    rotated = (
        await api.put("/integrations/gupshup?rotate_webhook=true", json=GUPSHUP, headers=headers)
    ).json()
    assert rotated["webhook_url"] and rotated["webhook_url"] != url
    moved = (
        await api.put(
            "/integrations/gupshup",
            json={**GUPSHUP, "source_number": gupshup_body()["source_number"]},
            headers=headers,
        )
    ).json()
    assert moved["webhook_url"]
    async with session_scope(runtime_factory, super_admin=True) as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM wa_numbers WHERE source_number = :s"),
                {"s": GUPSHUP["source_number"]},
            )
        ).scalar_one()
    assert count == 0


async def test_gupshup_number_taken_by_other_tenant(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h1, _ = await owner(api, mailer, runtime_factory)
    h2, _ = await owner(api, mailer, runtime_factory)
    GUPSHUP = gupshup_body()
    assert (await api.put("/integrations/gupshup", json=GUPSHUP, headers=h1)).status_code == 200
    clash = await api.put("/integrations/gupshup", json=GUPSHUP, headers=h2)
    assert clash.status_code == 409 and error_code(clash) == "E-CONF-001"


async def test_tenant_isolation_of_integrations(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h1, _ = await owner(api, mailer, runtime_factory)
    h2, _ = await owner(api, mailer, runtime_factory)
    await api.put("/integrations/shopify", json=SHOP, headers=h1)
    other = (await api.get("/integrations", headers=h2)).json()
    assert not other[0]["configured"]


async def test_probe_ok_and_not_configured(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    nc = await api.post("/integrations/shopify/test", headers=headers)
    assert nc.status_code == 400 and error_code(nc) == "E-INT-002"
    await api.put("/integrations/shopify", json=SHOP, headers=headers)
    ok = await api.post("/integrations/shopify/test", headers=headers)
    assert ok.json() == {"ok": True, "detail": None, "error_code": None}
    listed = (await api.get("/integrations", headers=headers)).json()
    assert listed[0]["status"] == "ok" and listed[0]["last_checked_at"]
    async with session_scope(runtime_factory, super_admin=True) as session:
        tenant_id = (
            (await session.execute(text("SELECT id FROM tenants ORDER BY created_at DESC")))
            .scalars()
            .first()
        )
    async with session_scope(runtime_factory, tenant_id=tenant_id) as session:
        calls = (
            await session.execute(text("SELECT provider, operation FROM external_calls"))
        ).all()
    assert ("shopify", "shop") in [tuple(c) for c in calls]


async def test_probe_auth_error_pauses_agent_and_audits(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, upstream: Upstream
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    await api.put("/integrations/shopify", json=SHOP, headers=headers)
    upstream.status = 401
    result = (await api.post("/integrations/shopify/test", headers=headers)).json()
    assert result["ok"] is False and result["error_code"] == "E-INT-001"
    status = (await api.get("/agent/status", headers=headers)).json()
    assert status["agent_paused"] is True and status["integration_errors"] == ["shopify"]
    async with session_scope(runtime_factory, super_admin=True) as session:
        actions = (await session.execute(text("SELECT action FROM audit_log"))).scalars().all()
    assert "agent.auto_paused" in actions
    upstream.status = 200
    assert (await api.post("/integrations/shopify/test", headers=headers)).json()["ok"] is True
    after = (await api.get("/agent/status", headers=headers)).json()
    assert after["integration_errors"] == [] and after["agent_paused"] is True


async def test_probe_upstream_failure_is_not_auth_error(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, upstream: Upstream
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    await api.put("/integrations/shopify", json=SHOP, headers=headers)
    upstream.status = 500
    result = (await api.post("/integrations/shopify/test", headers=headers)).json()
    assert result["ok"] is False
    status = (await api.get("/agent/status", headers=headers)).json()
    assert status["agent_paused"] is False and status["integration_errors"] == []


async def test_probe_couriers_and_unavailable_kinds(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, upstream: Upstream
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    await api.put("/integrations/delhivery", json={"credential": "tokentoken12"}, headers=headers)
    await api.put(
        "/integrations/shiprocket", json={"credential": "me@example.com:hunter22"}, headers=headers
    )
    await api.put("/integrations/gupshup", json=gupshup_body(), headers=headers)
    assert (await api.post("/integrations/shiprocket/test", headers=headers)).json()["ok"] is True
    assert (await api.post("/integrations/delhivery/test", headers=headers)).json()["ok"] is True
    gupshup = (await api.post("/integrations/gupshup/test", headers=headers)).json()
    assert gupshup["ok"] is True and "verify" in gupshup["detail"]
    upstream.status = 403
    assert (await api.post("/integrations/shiprocket/test", headers=headers)).json()["ok"] is False


async def test_notification_settings_and_agent_pause_resume(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    headers, _ = await owner(api, mailer, runtime_factory)
    default = (await api.get("/settings/notifications", headers=headers)).json()
    assert default["notify_email_enabled"] is True and default["notify_whatsapp_enabled"] is False
    body = {
        "notify_email_enabled": True,
        "notify_email": "ops@example.com",
        "notify_whatsapp_enabled": True,
        "alert_phone": "+919876543210",
        "grievance_email": "grievance@example.com",
    }
    assert (await api.put("/settings/notifications", json=body, headers=headers)).status_code == 200
    assert (await api.get("/settings/notifications", headers=headers)).json() == body
    bad = await api.put(
        "/settings/notifications", json={**body, "alert_phone": "98765"}, headers=headers
    )
    assert bad.status_code == 422
    paused = (await api.post("/agent/pause", headers=headers)).json()
    assert paused["agent_paused"] is True and paused["mode"] == "shadow"
    resumed = (await api.post("/agent/resume", headers=headers)).json()
    assert resumed["agent_paused"] is False and resumed["published_policy_version"] is None
    assert (await api.get("/agent/status")).status_code == 401


async def test_override_cleanup(api: httpx.AsyncClient) -> None:
    api._transport.app.dependency_overrides.pop(get_transport, None)  # type: ignore[attr-defined]
    assert get_session_factory in api._transport.app.dependency_overrides  # type: ignore[attr-defined]
