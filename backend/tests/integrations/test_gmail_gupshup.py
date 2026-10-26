import base64
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import Crypto
from app.core.db import session_scope
from app.integrations.gmail import GmailAuthError, GmailUpstreamError, HttpGmailClient
from app.integrations.gupshup import FakeGupshupSender, HttpGupshupSender, SendError
from app.integrations.http import ExternalHttp
from app.jobs import queue
from app.jobs.handlers import JobContext, fetch_gmail, integration_health
from app.worker import run_worker, schedule_periodic
from scripts.connect_gmail import connect_gmail
from tests.integrations.test_api import Upstream  # noqa: F401
from tests.jobs.test_queue import make_tenant

Factory = async_sessionmaker[AsyncSession]
Handler = Callable[[httpx.Request], httpx.Response]


async def _nosleep(_: float) -> None:
    return None


def http(handler: Handler) -> ExternalHttp:
    return ExternalHttp("t", transport=httpx.MockTransport(handler), sleep=_nosleep)


def b64(text_: str) -> str:
    return base64.urlsafe_b64encode(text_.encode()).decode().rstrip("=")


def gmail_msg(
    mid: str, sender: str = "buyer@example.com", body: str = "kahan hai order"
) -> dict[str, Any]:
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "internalDate": "1767000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "Order"}],
            "body": {"data": b64(body)},
        },
    }


class FakeGmail:
    def __init__(self, messages: dict[str, dict[str, Any]], auth_error: bool = False) -> None:
        self.messages = messages
        self.auth_error = auth_error
        self.gets: list[str] = []

    async def list_message_ids(self, query: str, limit: int) -> list[str]:
        if self.auth_error:
            raise GmailAuthError("x")
        return list(self.messages)[:limit]

    async def get_message(self, message_id: str) -> dict[str, Any]:
        self.gets.append(message_id)
        return self.messages[message_id]

    async def probe(self) -> bool:
        return not self.auth_error


@pytest.fixture
def google_env(auth_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    get_settings.cache_clear()


async def active_tenant_slug(factory: Factory) -> tuple[uuid.UUID, str]:
    tid = await make_tenant(factory)
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text("UPDATE tenants SET status = 'active' WHERE id = :i"), {"i": tid}
        )
        slug = (
            await session.execute(text("SELECT slug FROM tenants WHERE id = :i"), {"i": tid})
        ).scalar_one()
    return tid, slug


def job_for(tenant: uuid.UUID, kind: str) -> queue.Job:
    return queue.Job(uuid.uuid4(), tenant, kind, {}, 1, 3)


async def test_http_gmail_client_flow(google_env: None) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.host == "oauth2.googleapis.com":
            body = request.content.decode()
            assert "grant_type=refresh_token" in body and "client_id=cid" in body
            return httpx.Response(200, json={"access_token": "at-1"})
        assert request.headers["authorization"] == "Bearer at-1"
        if request.url.path.endswith("/messages"):
            assert request.url.params["q"] == "in:inbox"
            return httpx.Response(200, json={"messages": [{"id": "a"}, {"id": "b"}]})
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"id": "a"})

    client = HttpGmailClient(http(handler), "refresh-token-123")
    assert await client.list_message_ids("in:inbox", 5) == ["a", "b"]
    assert (await client.get_message("a"))["id"] == "a"
    assert await client.probe()
    assert calls.count("/token") == 1


async def test_http_gmail_client_errors(google_env: None) -> None:
    rejected = HttpGmailClient(
        http(lambda r: httpx.Response(400, json={"error": "invalid_grant"})), "r"
    )
    with pytest.raises(GmailAuthError):
        await rejected.list_message_ids("q", 1)
    assert not await HttpGmailClient(http(lambda r: httpx.Response(401)), "r").probe()

    def api_down(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(500)

    with pytest.raises(GmailUpstreamError):
        await HttpGmailClient(http(api_down), "r").list_message_ids("q", 1)
    forbidden = HttpGmailClient(
        http(
            lambda r: (
                httpx.Response(200, json={"access_token": "t"})
                if r.url.host.startswith("oauth")
                else httpx.Response(403)
            )
        ),
        "r",
    )
    with pytest.raises(GmailAuthError):
        await forbidden.get_message("x")


async def test_connect_gmail_script_encrypts_and_validates(
    runtime_factory: Factory, google_env: None
) -> None:
    tid, slug = await active_tenant_slug(runtime_factory)
    await connect_gmail(runtime_factory, slug, "Support@Brand.com", "refresh-token-abcdef")
    async with session_scope(runtime_factory, tenant_id=tid) as session:
        row = (
            await session.execute(
                text("SELECT config, secret_enc FROM integrations WHERE kind = 'gmail'")
            )
        ).one()
    assert row.config == {"address": "support@brand.com"}
    assert "refresh-token" not in row.secret_enc
    assert (
        json.loads(Crypto(get_settings().encryption_key).decrypt(row.secret_enc))["refresh_token"]
        == "refresh-token-abcdef"
    )
    from app.core.errors import AppError

    with pytest.raises(AppError):
        await connect_gmail(runtime_factory, slug, "nope", "refresh-token-abcdef")
    with pytest.raises(AppError):
        await connect_gmail(runtime_factory, "missing-slug", "a@b.com", "refresh-token-abcdef")


async def test_fetch_gmail_stores_dedupes_skips_own(
    runtime_factory: Factory, google_env: None
) -> None:
    tid, slug = await active_tenant_slug(runtime_factory)
    await connect_gmail(runtime_factory, slug, "support@brand.com", "refresh-token-abcdef")
    fake = FakeGmail(
        {
            "m1": gmail_msg("m1"),
            "m2": gmail_msg("m2", sender="Support <support@brand.com>"),
            "m3": {"id": "m3", "payload": {"headers": []}},
        }
    )
    ctx = JobContext(runtime_factory, job_for(tid, "fetch_gmail"), gmail_client=fake)
    assert await fetch_gmail(ctx) == {"fetched": 1}
    assert await fetch_gmail(ctx) == {"fetched": 0}
    assert fake.gets == ["m1", "m2", "m3"]
    async with session_scope(runtime_factory, tenant_id=tid) as session:
        events = {
            r.external_id: (r.status, r.skip_reason)
            for r in (
                await session.execute(
                    text("SELECT external_id, status, skip_reason FROM inbound_events")
                )
            ).all()
        }
        kinds = (await session.execute(text("SELECT kind FROM jobs"))).scalars().all()
    assert events == {
        "m1": ("queued", None),
        "m2": ("skipped", "OWN_ADDRESS"),
        "m3": ("skipped", "UNPARSEABLE"),
    }
    assert kinds == ["process_inbound"]


async def test_fetch_gmail_not_configured_and_auth_error(
    runtime_factory: Factory, google_env: None
) -> None:
    tid, slug = await active_tenant_slug(runtime_factory)
    ctx = JobContext(runtime_factory, job_for(tid, "fetch_gmail"), gmail_client=FakeGmail({}))
    assert (await fetch_gmail(ctx))["skipped"] == "not_configured"
    await connect_gmail(runtime_factory, slug, "support@brand.com", "refresh-token-abcdef")
    bad = JobContext(
        runtime_factory, job_for(tid, "fetch_gmail"), gmail_client=FakeGmail({}, auth_error=True)
    )
    assert await fetch_gmail(bad) == {"fetched": 0, "error": "auth"}
    async with session_scope(runtime_factory, tenant_id=tid) as session:
        status = (
            await session.execute(text("SELECT status FROM integrations WHERE kind = 'gmail'"))
        ).scalar_one()
    assert status == "auth_error"
    assert (await fetch_gmail(ctx))["skipped"] == "not_configured"


async def test_fetch_gmail_end_to_end_via_worker(
    runtime_factory: Factory, google_env: None
) -> None:
    tid, slug = await active_tenant_slug(runtime_factory)
    await connect_gmail(runtime_factory, slug, "support@brand.com", "refresh-token-abcdef")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "w1"}]})
        if request.url.path.endswith("/messages/w1"):
            return httpx.Response(200, json=gmail_msg("w1"))
        return httpx.Response(200, json={})

    stats = await run_worker(
        runtime_factory,
        30,
        transport=httpx.MockTransport(handler),
        idle_sleep=0.01,
        exit_when_idle=True,
    )
    assert stats["done"] >= 2
    async with session_scope(runtime_factory, tenant_id=tid) as session:
        events = (
            (await session.execute(text("SELECT external_id FROM inbound_events"))).scalars().all()
        )
        customers = (
            (await session.execute(text("SELECT email_lower FROM customers"))).scalars().all()
        )
        calls = (await session.execute(text("SELECT provider FROM external_calls"))).scalars().all()
    assert events == ["w1"] and customers == ["buyer@example.com"] and "gmail" in calls


async def test_schedule_periodic_idempotent_per_bucket(
    runtime_factory: Factory, google_env: None
) -> None:
    tid, slug = await active_tenant_slug(runtime_factory)
    await connect_gmail(runtime_factory, slug, "support@brand.com", "refresh-token-abcdef")
    now = datetime(2026, 5, 1, 10, 0, 30, tzinfo=UTC)
    first = await schedule_periodic(runtime_factory, now)
    assert first >= 2
    assert await schedule_periodic(runtime_factory, now) == 0
    async with session_scope(runtime_factory, tenant_id=tid) as session:
        kinds = sorted((await session.execute(text("SELECT kind FROM jobs"))).scalars().all())
    assert kinds == ["fetch_gmail", "integration_health", "retention"]


async def test_integration_health_handler_marks_auth_error(
    runtime_factory: Factory, google_env: None
) -> None:
    tid, slug = await active_tenant_slug(runtime_factory)
    await connect_gmail(runtime_factory, slug, "support@brand.com", "refresh-token-abcdef")
    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    ctx = JobContext(runtime_factory, job_for(tid, "integration_health"), transport=transport)
    assert await integration_health(ctx) == {"checked": {"gmail": False}}
    async with session_scope(runtime_factory, tenant_id=tid) as session:
        row = (
            await session.execute(text("SELECT status, last_error_code FROM integrations"))
        ).one()
        paused = (
            await session.execute(
                text("SELECT agent_paused FROM tenants WHERE id = :i"), {"i": tid}
            )
        ).scalar_one()
    assert (row.status, row.last_error_code) == ("auth_error", "E-INT-001") and paused is False


async def test_gmail_auth_error_notifies_owner_once(
    runtime_factory: Factory, google_env: None
) -> None:
    tid, slug = await active_tenant_slug(runtime_factory)
    await connect_gmail(runtime_factory, slug, "support@brand.com", "refresh-token-abcdef")
    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    ctx = JobContext(runtime_factory, job_for(tid, "integration_health"), transport=transport)
    await integration_health(ctx)
    await integration_health(ctx)
    async with session_scope(runtime_factory, tenant_id=tid) as session:
        titles = (
            (
                await session.execute(
                    text("SELECT title FROM notifications WHERE link = '/settings/integrations'")
                )
            )
            .scalars()
            .all()
        )
    assert titles == ["Gmail reconnect karna zaroori hai"]


async def test_gmail_invalid_client_is_config_error_not_reconnect(google_env: None) -> None:
    client = HttpGmailClient(
        http(lambda r: httpx.Response(401, json={"error": "invalid_client"})), "r"
    )
    with pytest.raises(GmailUpstreamError):
        await client.list_message_ids("q", 1)


async def test_http_gupshup_sender_and_fake() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(p.split("=", 1) for p in request.content.decode().split("&")))
        return httpx.Response(200, json={"response": {"status": "success", "id": "gs-1"}})

    sender = HttpGupshupSender(http(handler), "user", "pass")
    assert await sender.send_text("+919876543210", "hello") == "gs-1"
    assert seen["send_to"] == "919876543210" and seen["method"] == "SendMessage"
    cases: list[tuple[int, dict[str, Any], str]] = [
        (401, {}, "auth_error"),
        (500, {}, "server_error"),
        (200, {"response": {"status": "error"}}, "rejected"),
        (200, {"response": "bad"}, "bad_body"),
        (404, {}, "http_404"),
    ]
    for status, body, kind in cases:

        def respond(
            request: httpx.Request, s: int = status, b: dict[str, Any] = body
        ) -> httpx.Response:
            return httpx.Response(s, json=b)

        failing = HttpGupshupSender(http(respond), "u", "p")
        with pytest.raises(SendError) as info:
            await failing.send_text("+919876543210", "x")
        assert info.value.kind == kind
    fake = FakeGupshupSender()
    assert await fake.send_text("+911", "a") == "fake-1" and fake.sent == [("+911", "a")]
    fake.fail_with = "boom"
    with pytest.raises(SendError):
        await fake.send_text("+911", "b")
