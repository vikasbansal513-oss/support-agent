import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.contracts import PolicyV1
from app.core.db import session_scope
from app.policy.regression import RegressionResult
from tests.auth.helpers import MemoryMailer, active_user, bearer, error_code, login

Factory = async_sessionmaker[AsyncSession]


def content(**overrides: Any) -> dict[str, Any]:
    return {**PolicyV1().model_dump(mode="json"), **overrides}


async def owner(api: httpx.AsyncClient, mailer: MemoryMailer, factory: Factory) -> dict[str, str]:
    email = await active_user(api, mailer, factory)
    return bearer(await login(api, email))


async def test_create_list_update_draft(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    assert (await client.get("/policies", headers=h)).json() == []
    created = await client.post("/policies", json={"content": content()}, headers=h)
    assert created.status_code == 201 and created.json()["version"] == 1
    assert created.json()["status"] == "draft"
    pid = created.json()["id"]
    updated = await client.put(
        f"/policies/{pid}",
        json={"content": content(return_window_days=10), "base_version": 1},
        headers=h,
    )
    assert updated.status_code == 200 and updated.json()["content"]["return_window_days"] == 10
    second = await client.post(
        "/policies", json={"content": content(), "base_version": 1}, headers=h
    )
    assert second.json()["version"] == 2
    stale = await client.post(
        "/policies", json={"content": content(), "base_version": 1}, headers=h
    )
    assert stale.status_code == 409 and error_code(stale) == "E-CONF-001"
    stale_put = await client.put(
        f"/policies/{pid}", json={"content": content(), "base_version": 2}, headers=h
    )
    assert error_code(stale_put) == "E-CONF-001"
    assert [p["version"] for p in (await client.get("/policies", headers=h)).json()] == [2, 1]


async def test_invalid_content_is_e_pol_002(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    for bad in (
        content(return_window_days=0),
        content(refund_auto_limit_paise=100),
        content(enabled_tools=["order_lookup"]),
        {**content(), "unknown": 1},
    ):
        response = await client.post("/policies", json={"content": bad}, headers=h)
        assert response.status_code == 422 and error_code(response) == "E-POL-002"
    missing = await client.post("/policies", json={}, headers=h)
    assert error_code(missing) == "E-POL-002"


async def test_publish_flow_archives_previous_and_locks_edits(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    first = (await client.post("/policies", json={"content": content()}, headers=h)).json()
    reg = (await client.get(f"/policies/{first['id']}/regression", headers=h)).json()
    assert reg["passed"] and reg["scenarios_failed"] == [] and reg["scenarios_total"] >= 10
    pub = await client.post(f"/policies/{first['id']}/publish", headers=h)
    assert pub.status_code == 200 and pub.json()["status"] == "published"
    assert pub.json()["published_at"] and pub.json()["regression_run_id"]
    edit = await client.put(f"/policies/{first['id']}", json={"content": content()}, headers=h)
    assert edit.status_code == 409 and error_code(edit) == "E-POL-003"
    again = await client.post(f"/policies/{first['id']}/publish", headers=h)
    assert error_code(again) == "E-POL-003"
    second = (
        await client.post("/policies", json={"content": content(return_window_days=5)}, headers=h)
    ).json()
    assert (await client.post(f"/policies/{second['id']}/publish", headers=h)).status_code == 200
    statuses = {
        p["version"]: p["status"] for p in (await client.get("/policies", headers=h)).json()
    }
    assert statuses == {1: "archived", 2: "published"}
    status = (await client.get("/agent/status", headers=h)).json()
    assert status["published_policy_version"] == 2


async def test_publish_blocked_when_regression_fails(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await owner(client, mailer, runtime_factory)
    created = (await client.post("/policies", json={"content": content()}, headers=h)).json()
    monkeypatch.setattr(
        "app.policy.service.run_regression", lambda policy: RegressionResult(3, ["x_case"])
    )
    blocked = await client.post(f"/policies/{created['id']}/publish", headers=h)
    assert blocked.status_code == 409 and error_code(blocked) == "E-POL-001"
    listed = (await client.get("/policies", headers=h)).json()
    assert listed[0]["status"] == "draft"
    reg = (await client.get(f"/policies/{created['id']}/regression", headers=h)).json()
    assert reg["passed"] is False and reg["scenarios_failed"] == ["x_case"]
    async with session_scope(runtime_factory, super_admin=True) as session:
        actions = (await session.execute(text("SELECT action FROM audit_log"))).scalars().all()
    assert "policy.publish_blocked" in actions


async def test_edit_resets_regression_link_and_regression_stored(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    created = (await client.post("/policies", json={"content": content()}, headers=h)).json()
    await client.get(f"/policies/{created['id']}/regression", headers=h)
    listed = (await client.get("/policies", headers=h)).json()
    assert listed[0]["regression_run_id"]
    edited = await client.put(
        f"/policies/{created['id']}", json={"content": content(return_window_days=9)}, headers=h
    )
    assert edited.json()["regression_run_id"] is None


async def test_tenant_isolation_and_unknown_policy(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h1 = await owner(client, mailer, runtime_factory)
    h2 = await owner(client, mailer, runtime_factory)
    created = (await client.post("/policies", json={"content": content()}, headers=h1)).json()
    assert (await client.get("/policies", headers=h2)).json() == []
    for call in (
        client.put(f"/policies/{created['id']}", json={"content": content()}, headers=h2),
        client.post(f"/policies/{created['id']}/publish", headers=h2),
        client.get(f"/policies/{created['id']}/regression", headers=h2),
        client.post(f"/policies/{uuid.uuid4()}/publish", headers=h2),
    ):
        response = await call
        assert response.status_code == 404
    assert (await client.get("/policies")).status_code == 401


async def test_db_rejects_published_without_regression_and_two_published(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    created = (await client.post("/policies", json={"content": content()}, headers=h)).json()
    async with session_scope(runtime_factory, super_admin=True) as session:
        tenant_id = (
            await session.execute(
                text("SELECT tenant_id FROM policies WHERE id = :i"), {"i": created["id"]}
            )
        ).scalar_one()
    with pytest.raises(IntegrityError):
        async with session_scope(runtime_factory, tenant_id=tenant_id) as session:
            await session.execute(
                text("UPDATE policies SET status = 'published' WHERE id = :i"), {"i": created["id"]}
            )
