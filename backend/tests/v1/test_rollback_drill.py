from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from tests.agent.helpers import AgentEnv
from tests.auth.helpers import bearer
from tests.auth.test_admin_flow import _admin_login_with_mfa
from tests.v1.flow import actions, plan, say, setup


@pytest.fixture(autouse=True)
def _no_anomaly_stop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ANOMALY_AUTO_THRESHOLD", "1000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def admin_headers(env: AgentEnv) -> dict[str, str]:
    _, verified, _ = await _admin_login_with_mfa(env.client, env.factory)
    return bearer(verified)


async def test_lever_kill_switch_stops_a_pending_confirmation(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    await say(env, "order #1001 cancel kar do", "k1", plan())
    killed = await env.client.post(
        "/tools/order_cancel/kill", json={"reason": "drill: kill switch"}, headers=env.wa.headers
    )
    assert killed.status_code == 200
    await say(env, "haan", "k2")
    assert shop.calls == [] and shop.orders["#1001"].cancelled is False
    restored = await env.client.post(
        "/tools/order_cancel/restore", json={"reason": "drill: wapas chalu"}, headers=env.wa.headers
    )
    assert restored.status_code == 200
    state = {
        k["tool"]: k["killed"]
        for k in (await env.client.get("/tools/kill", headers=env.wa.headers)).json()
    }
    assert state["order_cancel"] is False
    assert len(await env.q("SELECT 1 FROM handoffs")) == 1


async def test_lever_admin_autonomy_back_to_approval_makes_next_write_wait(
    env: AgentEnv,
) -> None:
    _, shop = await setup(env, "auto")
    admin = await admin_headers(env)
    changed = await env.client.put(
        f"/admin/tenants/{env.wa.tenant_id}/autonomy",
        json={"tool": "order_cancel", "level": "approval", "reason": "drill: auto wapas"},
        headers=admin,
    )
    assert changed.status_code == 200 and changed.json()["levels"]["order_cancel"] == "approval"
    await say(env, "order #1001 cancel kar do", "a1", plan())
    await say(env, "haan", "a2")
    (action,) = await actions(env, "order_cancel")
    assert action.status == "pending_approval" and shop.calls == []


async def test_lever_admin_autonomy_off_hands_the_request_to_a_human(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    admin = await admin_headers(env)
    changed = await env.client.put(
        f"/admin/tenants/{env.wa.tenant_id}/autonomy",
        json={"tool": "order_cancel", "level": "off", "reason": "drill: tool band"},
        headers=admin,
    )
    assert changed.status_code == 200
    await say(env, "order #1001 cancel kar do", "o1", plan())
    assert await actions(env, "order_cancel") == [] and shop.calls == []
    assert (await env.q("SELECT reason_code FROM handoffs"))[0].reason_code == "POLICY_EXCEPTION"


async def test_lever_mode_back_to_shadow_creates_no_write_actions(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    await env.set_mode("shadow")
    await say(env, "order #1001 cancel kar do", "m1", plan())
    await say(env, "haan", "m2")
    assert await actions(env, "order_cancel") == [] and shop.calls == []


async def test_lever_global_pause_stops_agent_and_resume_restores_it(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    admin = await admin_headers(env)
    paused = await env.client.post(
        "/admin/global-pause", json={"reason": "drill: sab roko"}, headers=admin
    )
    assert paused.status_code == 200
    await say(env, "order #1001 cancel kar do", "g1", plan())
    await say(env, "haan", "g2")
    assert await actions(env, "order_cancel") == [] and shop.calls == []
    resumed = await env.client.post(
        "/admin/global-resume", json={"reason": "drill: sab chalu"}, headers=admin
    )
    assert resumed.status_code == 200
    await say(env, "order #1001 cancel kar do", "g3", plan())
    await say(env, "haan", "g4")
    assert shop.orders["#1001"].cancelled is True
