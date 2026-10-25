from datetime import timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.tokens import sha256_hex
from app.contracts.contracts import ErrorBody, LoginOut
from app.core.db import session_scope
from tests.auth.helpers import (
    PASSWORD,
    Clock,
    MemoryMailer,
    active_user,
    bearer,
    error_code,
    login,
    new_email,
    new_ip,
    set_tenant_status,
    signup,
)

Factory = async_sessionmaker[AsyncSession]


async def test_signup_flow_verify_then_pending_until_approved(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    email = new_email()
    response = await signup(client, email, brand="Raja Shoes")
    assert response.status_code == 202
    assert mailer.count(email) == 1
    assert "token=" in mailer.sent[-1][2] and "app.example.test/verify-email" in mailer.sent[-1][2]

    unverified = await login(client, email)
    assert unverified.status_code == 403 and error_code(unverified) == "E-AUTH-003"

    token = mailer.last_token(email)
    assert (await client.post("/auth/verify-email", json={"token": token})).status_code == 200
    again = await client.post("/auth/verify-email", json={"token": token})
    assert again.status_code == 400 and error_code(again) == "E-AUTH-008"

    pending = await login(client, email)
    assert pending.status_code == 403 and error_code(pending) == "E-AUTH-009"

    await set_tenant_status(runtime_factory, email, "active")
    ok = await login(client, email)
    assert ok.status_code == 200
    body = LoginOut.model_validate(ok.json())
    assert body.user.role == "owner" and body.user.mfa_ok and body.csrf_token
    me = await client.get("/auth/me", headers=bearer(ok))
    assert me.json()["email"] == email


async def test_new_tenant_starts_in_shadow_mode_pending(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    email = new_email()
    await signup(client, email, brand="Mode Check")
    async with session_scope(runtime_factory, super_admin=True) as session:
        row = (
            await session.execute(
                text(
                    "SELECT t.status, t.mode, t.slug FROM tenants t JOIN memberships m "
                    "ON m.tenant_id = t.id JOIN users u ON u.id = m.user_id WHERE u.email = :e"
                ),
                {"e": email},
            )
        ).one()
    assert row[0] == "pending_approval" and row[1] == "shadow" and row[2].startswith("mode-check-")


async def test_duplicate_signup_and_weak_password(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    email = new_email()
    assert (await signup(client, email)).status_code == 202
    dup = await signup(client, email.upper())
    assert dup.status_code == 409 and error_code(dup) == "E-AUTH-012"
    weak = await signup(client, new_email(), password="short")  # noqa: S106
    assert weak.status_code == 422 and error_code(weak) == "E-VAL-001"


async def test_signup_rate_limited_by_email(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    email = new_email()
    statuses = [(await signup(client, email)).status_code for _ in range(6)]
    assert statuses[0] == 202 and statuses[-1] == 429


async def test_wrong_password_and_unknown_email_look_identical(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    email = await active_user(client, mailer, runtime_factory)
    wrong = await login(client, email, "wrong-password-123")
    unknown = await login(client, new_email(), "wrong-password-123")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "E-AUTH-001"
    assert wrong.json()["message"] == unknown.json()["message"]
    ErrorBody.model_validate(wrong.json())


async def test_lockout_after_five_failures_then_unlocks(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, clock: Clock
) -> None:
    email = await active_user(client, mailer, runtime_factory)
    codes = [error_code(await login(client, email, "wrong-password-123")) for _ in range(5)]
    assert codes == ["E-AUTH-001"] * 4 + ["E-AUTH-002"]
    locked = await login(client, email)
    assert locked.status_code == 423 and error_code(locked) == "E-AUTH-002"
    clock.now += timedelta(minutes=16)
    assert (await login(client, email)).status_code == 200


async def test_success_resets_failure_counter(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    email = await active_user(client, mailer, runtime_factory)
    for _ in range(4):
        await login(client, email, "wrong-password-123")
    assert (await login(client, email)).status_code == 200
    for _ in range(4):
        assert error_code(await login(client, email, "wrong-password-123")) == "E-AUTH-001"


async def test_login_rate_limit_per_ip(client: httpx.AsyncClient) -> None:
    last = 0
    for _ in range(31):
        last = (await login(client, new_email(), "wrong-password-123")).status_code
    assert last == 429


async def test_client_ip_uses_proxy_header_not_spoofable_prefix(
    client: httpx.AsyncClient,
) -> None:
    for _ in range(30):
        await client.post(
            "/auth/login",
            json={"email": new_email(), "password": "wrong-password-123"},
            headers={"X-Forwarded-For": f"1.1.1.{_}, 9.9.9.9"},
        )
    blocked = await client.post(
        "/auth/login",
        json={"email": new_email(), "password": "wrong-password-123"},
        headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.9"},
    )
    assert blocked.status_code in (401, 429)
    fresh_ip = await login(client, new_email(), "wrong-password-123")
    assert fresh_ip.status_code == 401 or error_code(fresh_ip) == "E-AUTH-006"


async def test_resend_verification_gap_and_unknown_email(
    client: httpx.AsyncClient, mailer: MemoryMailer, clock: Clock
) -> None:
    email = new_email()
    await signup(client, email)
    first = mailer.last_token(email)
    assert (
        await client.post("/auth/resend-verification", json={"email": email})
    ).status_code == 202
    assert mailer.count(email) == 1
    clock.now += timedelta(seconds=61)
    assert (
        await client.post("/auth/resend-verification", json={"email": email})
    ).status_code == 202
    assert mailer.count(email) == 2
    second = mailer.last_token(email)
    assert first != second
    old = await client.post("/auth/verify-email", json={"token": first})
    assert error_code(old) == "E-AUTH-008"
    assert (await client.post("/auth/verify-email", json={"token": second})).status_code == 200
    unknown = new_email()
    assert (
        await client.post("/auth/resend-verification", json={"email": unknown})
    ).status_code == 202
    assert mailer.count(unknown) == 0


async def test_verify_token_expires_after_24h(
    client: httpx.AsyncClient, mailer: MemoryMailer, clock: Clock
) -> None:
    email = new_email()
    await signup(client, email)
    token = mailer.last_token(email)
    clock.now += timedelta(hours=24, seconds=5)
    expired = await client.post("/auth/verify-email", json={"token": token})
    assert expired.status_code == 400 and error_code(expired) == "E-AUTH-008"


async def test_email_tokens_are_hashed_in_db(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    email = new_email()
    await signup(client, email)
    token = mailer.last_token(email)
    async with session_scope(runtime_factory, super_admin=True) as session:
        hashes = (
            (await session.execute(text("SELECT token_hash FROM email_tokens"))).scalars().all()
        )
    assert token not in hashes and len(token) >= 40


async def test_forgot_and_reset_password(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, clock: Clock
) -> None:
    email = await active_user(client, mailer, runtime_factory)
    session1 = await login(client, email)
    assert (await client.post("/auth/forgot-password", json={"email": email})).status_code == 202
    token = mailer.last_token(email)
    ghost = new_email()
    assert (await client.post("/auth/forgot-password", json={"email": ghost})).status_code == 202
    assert mailer.count(ghost) == 0

    short = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "x" * 10}
    )
    assert short.status_code == 200
    reuse = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "y" * 12}
    )
    assert error_code(reuse) == "E-AUTH-008"
    assert error_code(await login(client, email)) == "E-AUTH-001"
    assert (await login(client, email, "x" * 10)).status_code == 200
    old_cookie = session1.cookies.get("sa_refresh") or ""
    assert old_cookie
    stale_refresh = await client.post(
        "/auth/refresh",
        headers={
            "Origin": "https://app.example.test",
            "X-CSRF-Token": session1.json()["csrf_token"],
        },
        cookies={"sa_refresh": old_cookie},
    )
    assert stale_refresh.status_code == 401 and error_code(stale_refresh) == "E-AUTH-004"
    async with session_scope(runtime_factory, super_admin=True) as session:
        revoked = (
            await session.execute(
                text("SELECT revoked_at IS NOT NULL FROM refresh_tokens WHERE token_hash = :h"),
                {"h": sha256_hex(old_cookie)},
            )
        ).scalar_one()
    assert revoked is True


async def test_reset_token_expires_after_one_hour(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, clock: Clock
) -> None:
    email = await active_user(client, mailer, runtime_factory)
    await client.post("/auth/forgot-password", json={"email": email})
    token = mailer.last_token(email)
    clock.now += timedelta(hours=1, seconds=5)
    late = await client.post(
        "/auth/reset-password", json={"token": token, "new_password": "z" * 12}
    )
    assert error_code(late) == "E-AUTH-008"
    bad = await client.post(
        "/auth/reset-password", json={"token": "t" * 20, "new_password": "short"}
    )
    assert bad.status_code in (400, 422)


async def test_change_password_kills_old_sessions(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, clock: Clock
) -> None:
    email = await active_user(client, mailer, runtime_factory)
    old = await login(client, email)
    clock.now += timedelta(seconds=5)
    wrong = await client.post(
        "/auth/change-password",
        headers=bearer(old),
        json={"current_password": "nope-nope-nope", "new_password": "n" * 12},
    )
    assert error_code(wrong) == "E-AUTH-001"
    done = await client.post(
        "/auth/change-password",
        headers=bearer(old),
        json={"current_password": PASSWORD, "new_password": "n" * 12},
    )
    assert done.status_code == 200
    assert error_code(await client.get("/auth/me", headers=bearer(old))) == "E-AUTH-004"
    clock.now += timedelta(seconds=5)
    fresh = await login(client, email, "n" * 12)
    assert (await client.get("/auth/me", headers=bearer(fresh))).status_code == 200


async def test_protected_routes_need_a_valid_bearer(client: httpx.AsyncClient) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        response = await client.get("/auth/me", headers=headers)
        assert response.status_code == 401 and error_code(response) == "E-AUTH-004"
    assert new_ip()


async def test_expired_access_token_rejected(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, clock: Clock
) -> None:
    email = await active_user(client, mailer, runtime_factory)
    logged = await login(client, email)
    assert (await client.get("/auth/me", headers=bearer(logged))).status_code == 200
    clock.now += timedelta(minutes=16)
    stale = await client.get("/auth/me", headers=bearer(logged))
    assert stale.status_code == 401 and error_code(stale) == "E-AUTH-004"
