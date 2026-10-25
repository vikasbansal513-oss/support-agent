from dataclasses import replace

from app.actions import writes
from app.integrations.shopify_write import set_writer_override
from app.jobs import ops
from tests.agent.helpers import AgentEnv
from tests.auth.helpers import error_code
from tests.ops.test_privacy import ctx
from tests.v1.helpers import make_conv, policy, propose_and_confirm, shop


async def publish_v2(env: AgentEnv) -> None:
    body = policy("approval").model_dump(mode="json")
    created = await env.client.post("/policies", json={"content": body}, headers=env.wa.headers)
    assert created.status_code == 201, created.text
    published = await env.client.post(
        f"/policies/{created.json()['id']}/publish", headers=env.wa.headers
    )
    assert published.status_code == 200, published.text


async def pending(env: AgentEnv) -> writes.ActionRow:
    conv = await make_conv(env)
    return await propose_and_confirm(env, conv, policy("approval"), route="approval")


async def test_list_approve_executes_once(env: AgentEnv) -> None:
    await publish_v2(env)
    fake = shop()
    set_writer_override(fake)
    try:
        action = await pending(env)
        assert action.status == "pending_approval"
        listed = await env.client.get("/approvals?status=pending_approval", headers=env.wa.headers)
        assert [a["id"] for a in listed.json()] == [str(action.id)]
        done = await env.client.post(f"/approvals/{action.id}/approve", headers=env.wa.headers)
        assert done.status_code == 200 and done.json()["status"] == "confirmed"
        assert fake.orders["#1001"].cancelled is True and len(fake.calls) == 1
        again = await env.client.post(f"/approvals/{action.id}/approve", headers=env.wa.headers)
        assert again.status_code == 409 and error_code(again) == "E-ACT-005"
    finally:
        set_writer_override(None)


async def test_reject_and_unknown_tool(env: AgentEnv) -> None:
    await publish_v2(env)
    action = await pending(env)
    bad = await env.client.post(
        f"/approvals/{action.id}/reject", json={"reason": "x"}, headers=env.wa.headers
    )
    assert bad.status_code == 422
    ok = await env.client.post(
        f"/approvals/{action.id}/reject", json={"reason": "galat request"}, headers=env.wa.headers
    )
    assert ok.json()["status"] == "rejected"
    nf = await env.client.post(
        "/tools/nope/kill", json={"reason": "testing"}, headers=env.wa.headers
    )
    assert nf.status_code == 404


async def test_kill_and_restore_tool(env: AgentEnv) -> None:
    await publish_v2(env)
    r = await env.client.post(
        "/tools/order_cancel/kill", json={"reason": "galat cancel ho rahe"}, headers=env.wa.headers
    )
    assert r.json()["killed"] is True
    state = {
        t["tool"]: t["killed"]
        for t in (await env.client.get("/tools/kill", headers=env.wa.headers)).json()
    }
    assert state["order_cancel"] is True and state["refund_create"] is False
    restored = await env.client.post(
        "/tools/order_cancel/restore", json={"reason": "theek ho gaya"}, headers=env.wa.headers
    )
    assert restored.json()["killed"] is False
    audit = await env.q(
        "SELECT action FROM audit_log WHERE action LIKE 'tool.%' ORDER BY occurred_at"
    )
    assert [a.action for a in audit] == ["tool.killed", "tool.restored"]


async def test_approve_blocked_when_order_changed(env: AgentEnv) -> None:
    await publish_v2(env)
    fake = shop()
    set_writer_override(fake)
    try:
        action = await pending(env)
        fake.orders["#1001"] = replace(fake.orders["#1001"], total_paise=99_000)
        out = await env.client.post(f"/approvals/{action.id}/approve", headers=env.wa.headers)
        assert out.json()["status"] == "rejected" and out.json()["detail"] == "ORDER_CHANGED"
        assert fake.calls == []
    finally:
        set_writer_override(None)


async def test_upkeep_expires_stale_confirmations(env: AgentEnv) -> None:
    conv = await make_conv(env)
    await propose_and_confirm(env, conv, policy("approval"), route="approval")
    out = await ops.write_upkeep(ctx(env, "write_upkeep"))
    assert out == {"expired": 0, "reconciled": 0, "resumed": 0}
