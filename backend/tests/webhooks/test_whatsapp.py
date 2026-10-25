import json
import logging

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import session_scope
from tests.auth.helpers import MemoryMailer
from tests.channels.helpers import WaTenant, setup_wa_tenant, wa_message

Factory = async_sessionmaker[AsyncSession]


async def tenant(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    app_name: str = "",
) -> WaTenant:
    return await setup_wa_tenant(client, mailer, factory, monkeypatch, app_name)


async def events(factory: Factory, t: WaTenant) -> list[tuple[str, str | None, str]]:
    async with session_scope(factory, tenant_id=t.tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT status, skip_reason, external_id FROM inbound_events ORDER BY received_at"
                )
            )
        ).all()
    return [(r.status, r.skip_reason, r.external_id) for r in rows]


async def jobs(factory: Factory, t: WaTenant) -> list[str]:
    async with session_scope(factory, tenant_id=t.tenant_id) as session:
        return list((await session.execute(text("SELECT kind FROM jobs"))).scalars().all())


async def test_message_stored_queued_and_job_created(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch)
    response = await client.post(t.path, json=wa_message("m-1"))
    assert response.status_code == 200 and response.json() == {"status": "queued"}
    assert await events(runtime_factory, t) == [("queued", None, "m-1")]
    assert await jobs(runtime_factory, t) == ["process_inbound"]
    async with session_scope(runtime_factory, tenant_id=t.tenant_id) as session:
        raw = (await session.execute(text("SELECT raw_payload FROM inbound_events"))).scalar_one()
    assert raw["normalized"]["from_phone"] == "+919876543210"


async def test_duplicate_webhook_is_idempotent(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch)
    first = await client.post(t.path, json=wa_message("dup-1"))
    second = await client.post(t.path, json=wa_message("dup-1"))
    assert first.json()["status"] == "queued" and second.json() == {"status": "duplicate"}
    assert len(await events(runtime_factory, t)) == 1 and len(await jobs(runtime_factory, t)) == 1


async def test_wrong_token_404_without_leak(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch)
    response = await client.post("/webhooks/whatsapp/" + "x" * 43, json=wa_message())
    assert response.status_code == 404 and response.json()["code"] == "E-WEB-001"
    assert str(t.tenant_id) not in response.text
    assert await events(runtime_factory, t) == []


async def test_status_events_and_garbage_skipped_with_200(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch)
    status_event = {"type": "message-event", "app": "BrandApp", "payload": {"type": "delivered"}}
    assert (await client.post(t.path, json=status_event)).json() == {"status": "skipped"}
    assert (
        await client.post(t.path, content=b"not json", headers={"content-type": "text/plain"})
    ).status_code == 200
    assert (await client.post(t.path, json=[1, 2])).status_code == 200
    got = await events(runtime_factory, t)
    assert [e[1] for e in got] == ["NOT_A_CUSTOMER_MESSAGE", "UNPARSEABLE", "UNPARSEABLE"]
    assert await jobs(runtime_factory, t) == []
    assert (await client.post(t.path, json=status_event)).json() == {"status": "duplicate"}


async def test_app_name_mismatch_skipped(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch, app_name="MyBrand")
    assert (await client.post(t.path, json=wa_message(app="Other"))).json() == {"status": "skipped"}
    assert (await events(runtime_factory, t))[0][1] == "APP_MISMATCH"
    assert (await client.post(t.path, json=wa_message(app="MyBrand"))).json() == {
        "status": "queued"
    }


async def test_oversized_body_not_stored(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch)
    big = json.dumps({"type": "message", "pad": "x" * 300_000}).encode()
    response = await client.post(t.path, content=big, headers={"content-type": "application/json"})
    assert response.status_code == 200 and await events(runtime_factory, t) == []


async def test_ip_allowlist(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch)
    monkeypatch.setenv("GUPSHUP_IP_ALLOWLIST", "203.0.113.9")
    get_settings.cache_clear()
    blocked = await client.post(t.path, json=wa_message())
    assert blocked.status_code == 403 and blocked.json()["code"] == "E-WEB-001"
    ok = await client.post(t.path, json=wa_message(), headers={"X-Forwarded-For": "203.0.113.9"})
    assert ok.status_code == 200


async def test_internal_error_generic_body(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = await tenant(client, mailer, runtime_factory, monkeypatch)

    async def boom(*_: object, **__: object) -> str:
        raise RuntimeError("db exploded with secret-detail")

    monkeypatch.setattr("app.webhooks.whatsapp._store", boom)
    response = await client.post(t.path, json=wa_message())
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal" and body["error_id"].startswith("ERR-")
    assert "secret-detail" not in response.text


def test_token_scrubbed_from_logs() -> None:
    from app.core.logging import JsonFormatter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "POST /webhooks/whatsapp/AbC_123-token HTTP/1.1",
        (),
        None,
    )
    line = JsonFormatter().format(record)
    assert "AbC_123-token" not in line and "/webhooks/whatsapp/[REDACTED]" in line
