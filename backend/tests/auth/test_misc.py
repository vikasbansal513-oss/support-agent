import json
import logging

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.mail import BrevoMailer, send_safely
from app.main import create_app
from scripts.seed_admin import seed_admin
from tests.auth.helpers import (
    PASSWORD,
    MemoryMailer,
    active_user,
    bearer,
    error_code,
    login,
    make_admin,
    new_email,
    totp_now,
)

Factory = async_sessionmaker[AsyncSession]


async def test_cors_allows_only_frontend_origin(
    client: httpx.AsyncClient,
) -> None:
    ok = await client.options(
        "/auth/login",
        headers={
            "Origin": "https://app.example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://app.example.test"
    assert ok.headers["access-control-allow-credentials"] == "true"
    assert "PATCH" in ok.headers["access-control-allow-methods"]
    evil = await client.options(
        "/auth/login",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in evil.headers
    bad_header = await client.options(
        "/auth/login",
        headers={
            "Origin": "https://app.example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-evil",
        },
    )
    assert bad_header.status_code == 400


async def test_cors_closed_when_no_frontend_configured(
    runtime_factory: Factory, monkeypatch: pytest.MonkeyPatch, auth_env: None
) -> None:
    monkeypatch.setenv("FRONTEND_URL", "")
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_session_factory] = lambda: runtime_factory
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://test"
    ) as http:
        response = await http.options(
            "/auth/login",
            headers={"Origin": "https://app.example.test", "Access-Control-Request-Method": "POST"},
        )
    assert "access-control-allow-origin" not in response.headers


async def test_seed_admin_is_idempotent_and_can_login(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    email = new_email()
    await seed_admin(runtime_factory, email.upper(), "Boss", PASSWORD)
    await seed_admin(runtime_factory, email, "Boss", PASSWORD)
    async with session_scope(runtime_factory, super_admin=True) as session:
        count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM platform_admins a JOIN users u ON u.id = a.user_id "
                    "WHERE u.email = :e"
                ),
                {"e": email},
            )
        ).scalar_one()
    assert count == 1
    assert (await login(client, email, admin=True)).status_code == 200


async def test_seed_admin_rejects_weak_password(runtime_factory: Factory) -> None:
    from app.core.errors import AppError

    with pytest.raises(AppError):
        await seed_admin(runtime_factory, new_email(), "Boss", "short")


async def test_tenant_session_dependency_scopes_rows_to_the_caller(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
) -> None:
    from fastapi import Depends, FastAPI

    from app.auth.deps import tenant_session
    from app.core.errors import install_error_handlers

    probe = FastAPI()
    install_error_handlers(probe)

    @probe.get("/whoami")
    async def whoami(session: AsyncSession = Depends(tenant_session)) -> dict[str, int]:  # noqa: B008
        count = (await session.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
        return {"visible_tenants": int(count)}

    probe.dependency_overrides[get_session_factory] = lambda: runtime_factory
    email = await active_user(client, mailer, runtime_factory)
    await active_user(client, mailer, runtime_factory)
    logged = await login(client, email)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=probe), base_url="https://test"
    ) as other:
        mine = await other.get("/whoami", headers=bearer(logged))
        assert mine.json() == {"visible_tenants": 1}
        anonymous = await other.get("/whoami")
        assert error_code(anonymous) == "E-AUTH-004"


async def test_admin_token_cannot_use_tenant_session(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    from fastapi import Depends, FastAPI

    from app.auth.deps import tenant_session
    from app.core.errors import install_error_handlers

    probe = FastAPI()
    install_error_handlers(probe)

    @probe.get("/x")
    async def route(session: AsyncSession = Depends(tenant_session)) -> dict[str, str]:  # noqa: B008
        return {"ok": "1"}

    probe.dependency_overrides[get_session_factory] = lambda: runtime_factory
    email = await make_admin(runtime_factory)
    first = await login(client, email, admin=True)
    secret = (await client.post("/auth/mfa/setup", headers=bearer(first))).json()["secret"]
    await client.post("/auth/mfa/confirm", headers=bearer(first), json={"code": totp_now(secret)})
    client.cookies.clear()
    challenge = await login(client, email, admin=True)
    verified = await client.post(
        "/admin/auth/mfa/verify",
        json={"mfa_token": challenge.json()["mfa_token"], "code": totp_now(secret, 1)},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=probe), base_url="https://test"
    ) as other:
        response = await other.get("/x", headers=bearer(verified))
    assert response.status_code == 403 and error_code(response) == "E-AUTH-007"


async def test_brevo_mailer_sends_expected_request(
    auth_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREVO_API_KEY", "k" * 20)
    monkeypatch.setenv("MAIL_FROM", "noreply@example.com")
    get_settings.cache_clear()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"messageId": "1"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.core.mail.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    await BrevoMailer().send("a@example.com", "Hi", "Body")
    assert seen["url"] == "https://api.brevo.com/v3/smtp/email"
    assert seen["key"] == "k" * 20
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["to"] == [{"email": "a@example.com"}] and body["textContent"] == "Body"


async def test_mail_failures_never_break_the_request_or_leak(
    auth_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        async def send(self, to: str, subject: str, text: str) -> None:
            raise RuntimeError("provider said token=SECRETVALUE for a@example.com")

    caplog.set_level(logging.ERROR)
    assert await send_safely(Broken(), "a@example.com", "s", "t") is False
    assert "SECRETVALUE" not in caplog.text and "a@example.com" not in caplog.text


async def test_unconfigured_mailer_fails_safely(auth_env: None) -> None:
    assert await send_safely(BrevoMailer(), "a@example.com", "s", "t") is False


async def test_logs_never_contain_passwords_or_tokens(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    email = await active_user(client, mailer, runtime_factory)
    logged = await login(client, email)
    await login(client, email, "wrong-password-xyz")
    await client.post("/auth/forgot-password", json={"email": email})
    reset_token = mailer.last_token(email)
    access = logged.json()["access_token"]
    refresh_cookie = client.cookies.get("sa_refresh") or ""
    out = capfd.readouterr().out + caplog.text
    for secret in (PASSWORD, "wrong-password-xyz", reset_token, access, refresh_cookie):
        assert secret and secret not in out
    assert email not in out
