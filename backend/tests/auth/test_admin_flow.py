import uuid

import httpx
import pyotp
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.contracts import TenantOut
from app.core.db import session_scope
from tests.auth.helpers import (
    ORIGIN,
    MemoryMailer,
    active_user,
    bearer,
    error_code,
    login,
    make_admin,
    new_email,
    refresh_headers,
    signup,
    totp_now,
)

Factory = async_sessionmaker[AsyncSession]


async def _admin_login_with_mfa(
    client: httpx.AsyncClient, factory: Factory
) -> tuple[str, httpx.Response, str]:
    email = await make_admin(factory)
    first = await login(client, email, admin=True)
    assert first.status_code == 200 and first.json()["user"]["mfa_ok"] is False
    setup = await client.post("/auth/mfa/setup", headers=bearer(first))
    secret = setup.json()["secret"]
    confirm = await client.post(
        "/auth/mfa/confirm", headers=bearer(first), json={"code": totp_now(secret)}
    )
    assert confirm.status_code == 200 and len(confirm.json()["recovery_codes"]) == 10
    client.cookies.clear()
    challenge = await login(client, email, admin=True)
    assert challenge.json()["mfa_required"] is True
    verify = await client.post(
        "/admin/auth/mfa/verify",
        json={"mfa_token": challenge.json()["mfa_token"], "code": totp_now(secret, 1)},
    )
    assert verify.status_code == 200 and verify.json()["user"]["mfa_ok"] is True
    return email, verify, secret


async def test_admin_without_mfa_is_restricted_until_mfa_enabled(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    email = await make_admin(runtime_factory)
    logged = await login(client, email, admin=True)
    assert logged.json()["user"]["role"] == "super_admin"
    blocked = await client.get("/admin/tenants", headers=bearer(logged))
    assert blocked.status_code == 401 and error_code(blocked) == "E-AUTH-005"
    status = await client.get("/auth/mfa", headers=bearer(logged))
    assert status.json() == {"enabled": False, "required": True, "recovery_remaining": 0}


async def test_admin_full_mfa_login_and_tenant_listing(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    _, verified, _ = await _admin_login_with_mfa(client, runtime_factory)
    listing = await client.get("/admin/tenants", headers=bearer(verified))
    assert listing.status_code == 200 and "items" in listing.json()


async def test_wrong_mfa_code_rejected_and_attempts_limited(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    email, _, secret = await _admin_login_with_mfa(client, runtime_factory)
    client.cookies.clear()
    challenge = await login(client, email, admin=True)
    token = challenge.json()["mfa_token"]
    codes = []
    for _ in range(6):
        response = await client.post(
            "/admin/auth/mfa/verify", json={"mfa_token": token, "code": "000000"}
        )
        codes.append(error_code(response))
    assert codes[0] == "E-AUTH-005" and "E-AUTH-006" in codes
    first_block = codes.index("E-AUTH-006")
    assert set(codes[:first_block]) == {"E-AUTH-005"} and set(codes[first_block:]) == {"E-AUTH-006"}


async def test_recovery_code_logs_in_once(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    email = await make_admin(runtime_factory)
    first = await login(client, email, admin=True)
    secret = (await client.post("/auth/mfa/setup", headers=bearer(first))).json()["secret"]
    codes = (
        await client.post(
            "/auth/mfa/confirm", headers=bearer(first), json={"code": totp_now(secret)}
        )
    ).json()["recovery_codes"]
    client.cookies.clear()
    challenge = await login(client, email, admin=True)
    ok = await client.post(
        "/admin/auth/mfa/verify",
        json={"mfa_token": challenge.json()["mfa_token"], "code": codes[0]},
    )
    assert ok.status_code == 200
    challenge2 = await login(client, email, admin=True)
    reused = await client.post(
        "/admin/auth/mfa/verify",
        json={"mfa_token": challenge2.json()["mfa_token"], "code": codes[0]},
    )
    assert error_code(reused) == "E-AUTH-005"


async def test_mfa_enable_revokes_existing_sessions(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    email = await make_admin(runtime_factory)
    first = await login(client, email, admin=True)
    cookie = client.cookies.get("sa_admin_refresh") or ""
    secret = (await client.post("/auth/mfa/setup", headers=bearer(first))).json()["secret"]
    await client.post("/auth/mfa/confirm", headers=bearer(first), json={"code": totp_now(secret)})
    client.cookies.clear()
    stale = await client.post(
        "/admin/auth/refresh",
        headers=refresh_headers(first),
        cookies={"sa_admin_refresh": cookie},
    )
    assert stale.status_code == 401


async def test_realms_are_isolated(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    brand_email = await active_user(client, mailer, runtime_factory)
    brand = await login(client, brand_email)
    wrong_realm = await client.get("/admin/tenants", headers=bearer(brand))
    assert wrong_realm.status_code == 403 and error_code(wrong_realm) == "E-ADM-001"
    as_admin = await login(client, brand_email, admin=True)
    assert as_admin.status_code == 401 and error_code(as_admin) == "E-AUTH-001"

    _, admin, _ = await _admin_login_with_mfa(client, runtime_factory)
    admin_email = admin.json()["user"]["email"]
    as_brand = await login(client, admin_email)
    assert as_brand.status_code == 403 and error_code(as_brand) == "E-AUTH-007"
    assert (await client.get("/auth/me", headers=bearer(admin))).json()["role"] == "super_admin"


async def test_admin_cookie_name_and_path_differ(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    email = await make_admin(runtime_factory)
    logged = await login(client, email, admin=True)
    header = logged.headers["set-cookie"].lower()
    assert "sa_admin_refresh=" in header and "path=/admin/auth" in header
    brand_path = await client.post("/auth/refresh", headers=refresh_headers(logged))
    assert brand_path.status_code == 401


async def test_admin_refresh_and_logout(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    _, verified, _ = await _admin_login_with_mfa(client, runtime_factory)
    refreshed = await client.post("/admin/auth/refresh", headers=refresh_headers(verified))
    assert refreshed.status_code == 200 and refreshed.json()["user"]["mfa_ok"] is True
    out = await client.post("/admin/auth/logout", headers=refresh_headers(refreshed))
    assert out.status_code == 204


async def test_tenant_approval_pause_resume_patch_and_audit(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    _, admin, _ = await _admin_login_with_mfa(client, runtime_factory)
    headers = bearer(admin)
    email = new_email()
    await signup(client, email, brand="Approve Me")
    async with session_scope(runtime_factory, super_admin=True) as session:
        tenant_id = (
            await session.execute(
                text(
                    "SELECT t.id FROM tenants t JOIN memberships m ON m.tenant_id = t.id "
                    "JOIN users u ON u.id = m.user_id WHERE u.email = :e"
                ),
                {"e": email},
            )
        ).scalar_one()
    base = f"/admin/tenants/{tenant_id}"

    got = await client.get(base, headers=headers)
    assert TenantOut.model_validate(got.json()).status == "pending_approval"
    short = await client.post(f"{base}/approve", headers=headers, json={"reason": "no"})
    assert short.status_code == 422
    approved = await client.post(f"{base}/approve", headers=headers, json={"reason": "KYC checked"})
    assert approved.status_code == 200 and approved.json()["status"] == "active"
    assert approved.json()["approved_at"] is not None
    again = await client.post(f"{base}/approve", headers=headers, json={"reason": "KYC checked"})
    assert again.status_code == 409 and error_code(again) == "E-CONF-001"

    paused = await client.post(f"{base}/pause", headers=headers, json={"reason": "billing hold"})
    assert paused.json()["status"] == "paused"
    assert (
        await client.post(f"{base}/pause", headers=headers, json={"reason": "again again"})
    ).status_code == 409
    resumed = await client.post(f"{base}/resume", headers=headers, json={"reason": "paid up"})
    assert resumed.json()["status"] == "active"

    patched = await client.patch(
        base, headers=headers, json={"name": "  New Name  ", "notes": "vip"}
    )
    assert patched.json()["name"] == "New Name"
    extra = await client.patch(base, headers=headers, json={"slug": "hack"})
    assert extra.status_code == 422

    limit = await client.put(
        f"{base}/usage-limit",
        headers=headers,
        json={"usage_override_limit": 900, "reason": "promo"},
    )
    assert limit.json()["usage_override_limit"] == 900
    cleared = await client.put(
        f"{base}/usage-limit",
        headers=headers,
        json={"usage_override_limit": None, "reason": "promo over"},
    )
    assert cleared.json()["usage_override_limit"] is None

    missing = await client.get(f"/admin/tenants/{uuid.uuid4()}", headers=headers)
    assert missing.status_code == 404 and error_code(missing) == "E-NF-001"

    async with session_scope(runtime_factory, super_admin=True) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action, reason, tenant_id FROM audit_log WHERE entity_id = :i "
                    "AND action LIKE 'admin.%' ORDER BY occurred_at"
                ),
                {"i": str(tenant_id)},
            )
        ).all()
    actions = [r[0] for r in rows]
    assert actions == [
        "admin.tenant_approved",
        "admin.tenant_paused",
        "admin.tenant_resumed",
        "admin.tenant_patched",
        "admin.usage_limit_changed",
        "admin.usage_limit_changed",
    ]
    assert all(r[2] == tenant_id for r in rows)


async def test_tenant_listing_filter_and_cursor_paging(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    _, admin, _ = await _admin_login_with_mfa(client, runtime_factory)
    headers = bearer(admin)
    for index in range(3):
        await signup(client, new_email(), brand=f"Paged Brand {index}")
    seen: list[str] = []
    cursor = None
    for _ in range(50):
        params: dict[str, str | int] = {"limit": 2, "status": "pending_approval"}
        if cursor:
            params["cursor"] = cursor
        page = await client.get("/admin/tenants", headers=headers, params=params)
        body = page.json()
        assert len(body["items"]) <= 2
        seen.extend(item["id"] for item in body["items"])
        assert all(item["status"] == "pending_approval" for item in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) and len(seen) >= 3
    bad = await client.get("/admin/tenants", headers=headers, params={"cursor": "!!!"})
    assert bad.status_code == 422 and error_code(bad) == "E-VAL-001"
    unauth = await client.get("/admin/tenants")
    assert unauth.status_code == 401


async def test_unknown_origin_cannot_use_cookie_endpoints_even_with_valid_csrf(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    email = await make_admin(runtime_factory)
    logged = await login(client, email, admin=True)
    response = await client.post(
        "/admin/auth/refresh",
        headers={"Origin": "https://evil.test", "X-CSRF-Token": logged.json()["csrf_token"]},
    )
    assert error_code(response) == "E-AUTH-010"
    assert ORIGIN
    assert pyotp.random_base32()
