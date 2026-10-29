from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.auth.helpers import (
    ORIGIN,
    Clock,
    MemoryMailer,
    active_user,
    bearer,
    error_code,
    login,
    refresh_headers,
    set_tenant_status,
)

Factory = async_sessionmaker[AsyncSession]


def _cookie(response: httpx.Response) -> str:
    return response.cookies.get("sa_refresh") or ""


async def _session(
    client: httpx.AsyncClient, mailer: MemoryMailer, factory: Factory
) -> tuple[str, httpx.Response]:
    email = await active_user(client, mailer, factory)
    client.cookies.clear()
    return email, await login(client, email)


async def test_refresh_cookie_attributes_and_no_token_in_cookie(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    _, logged = await _session(client, mailer, runtime_factory)
    header = logged.headers["set-cookie"].lower()
    assert "sa_refresh=" in header and "httponly" in header and "secure" in header
    assert "samesite=none" in header and "path=/" in header
    assert logged.json()["access_token"] not in header


async def test_refresh_rotates_and_reuse_revokes_family(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    _, logged = await _session(client, mailer, runtime_factory)
    first_cookie = _cookie(logged)
    second = await client.post("/auth/refresh", headers=refresh_headers(logged))
    assert second.status_code == 200
    second_cookie = _cookie(second)
    assert second_cookie and second_cookie != first_cookie
    assert second.json()["csrf_token"] != logged.json()["csrf_token"]
    assert (await client.get("/auth/me", headers=bearer(second))).status_code == 200

    stolen = await client.post(
        "/auth/refresh",
        headers=refresh_headers(logged),
        cookies={"sa_refresh": first_cookie},
    )
    assert stolen.status_code == 401 and error_code(stolen) == "E-AUTH-004"

    client.cookies.clear()
    family_dead = await client.post(
        "/auth/refresh",
        headers=refresh_headers(second),
        cookies={"sa_refresh": second_cookie},
    )
    assert family_dead.status_code == 401


async def test_refresh_requires_origin_csrf_and_cookie(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    _, logged = await _session(client, mailer, runtime_factory)
    good_csrf = logged.json()["csrf_token"]

    no_origin = await client.post("/auth/refresh", headers={"X-CSRF-Token": good_csrf})
    assert no_origin.status_code == 403 and error_code(no_origin) == "E-AUTH-010"
    evil = await client.post(
        "/auth/refresh", headers={"Origin": "https://evil.test", "X-CSRF-Token": good_csrf}
    )
    assert error_code(evil) == "E-AUTH-010"
    no_csrf = await client.post("/auth/refresh", headers={"Origin": ORIGIN})
    assert error_code(no_csrf) == "E-AUTH-010"
    wrong_csrf = await client.post(
        "/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": "0" * 64}
    )
    assert error_code(wrong_csrf) == "E-AUTH-010"

    client.cookies.clear()
    no_cookie = await client.post("/auth/refresh", headers=refresh_headers(logged))
    assert no_cookie.status_code == 401 and error_code(no_cookie) == "E-AUTH-004"


async def test_csrf_from_another_session_is_rejected(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    _, mine = await _session(client, mailer, runtime_factory)
    _, other = await _session(client, mailer, runtime_factory)
    client.cookies.clear()
    response = await client.post(
        "/auth/refresh",
        headers=refresh_headers(other),
        cookies={"sa_refresh": _cookie(mine)},
    )
    assert error_code(response) == "E-AUTH-010"


async def test_logout_revokes_session_and_clears_cookie(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    _, logged = await _session(client, mailer, runtime_factory)
    cookie = _cookie(logged)
    bad = await client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": "x"})
    assert error_code(bad) == "E-AUTH-010"
    out = await client.post("/auth/logout", headers=refresh_headers(logged))
    assert out.status_code == 204
    assert (
        "sa_refresh=" in out.headers["set-cookie"]
        and "max-age=0" in out.headers["set-cookie"].lower()
    )
    client.cookies.clear()
    reuse = await client.post(
        "/auth/refresh", headers=refresh_headers(logged), cookies={"sa_refresh": cookie}
    )
    assert reuse.status_code == 401


async def test_refresh_token_expires(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, clock: Clock
) -> None:
    _, logged = await _session(client, mailer, runtime_factory)
    clock.now += timedelta(days=14, seconds=5)
    expired = await client.post("/auth/refresh", headers=refresh_headers(logged))
    assert expired.status_code == 401 and error_code(expired) == "E-AUTH-004"


async def test_paused_tenant_rejected_immediately_everywhere(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    email, logged = await _session(client, mailer, runtime_factory)
    assert (await client.get("/auth/me", headers=bearer(logged))).status_code == 200
    await set_tenant_status(runtime_factory, email, "paused")
    blocked = await client.get("/auth/me", headers=bearer(logged))
    assert blocked.status_code == 403 and error_code(blocked) == "E-AUTH-009"
    refresh = await client.post("/auth/refresh", headers=refresh_headers(logged))
    assert refresh.status_code == 401
    assert error_code(await login(client, email)) == "E-AUTH-009"
    await set_tenant_status(runtime_factory, email, "active")
    assert (await login(client, email)).status_code == 200


async def test_suspended_tenant_blocked(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    email, logged = await _session(client, mailer, runtime_factory)
    await set_tenant_status(runtime_factory, email, "suspended")
    assert error_code(await client.get("/auth/me", headers=bearer(logged))) == "E-AUTH-009"
