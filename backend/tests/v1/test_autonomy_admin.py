from datetime import timedelta

from sqlalchemy import text

from app.core import timeutil
from app.core.db import session_scope
from app.jobs import ops
from tests.agent.helpers import AgentEnv
from tests.auth.helpers import bearer, error_code
from tests.auth.test_admin_flow import _admin_login_with_mfa
from tests.ops.test_privacy import ctx
from tests.v1.flow import setup
from tests.v1.helpers import policy as v2_policy


async def admin_headers(env: AgentEnv) -> dict[str, str]:
    _, verified, _ = await _admin_login_with_mfa(env.client, env.factory)
    return bearer(verified)


async def levels(env: AgentEnv) -> dict[str, str]:
    listed = (await env.client.get("/policies", headers=env.wa.headers)).json()
    published = next(p for p in listed if p["status"] == "published")
    result: dict[str, str] = published["content"]["autonomy"]
    return result


async def test_admin_puts_one_tool_on_auto_and_it_is_audited(env: AgentEnv) -> None:
    await setup(env, "approval", mode="auto")
    admin = await admin_headers(env)
    before = len((await env.client.get("/policies", headers=env.wa.headers)).json())
    out = await env.client.put(
        f"/admin/tenants/{env.wa.tenant_id}/autonomy",
        json={"tool": "order_cancel", "level": "auto", "reason": "pilot ke baad approve"},
        headers=admin,
    )
    assert out.status_code == 200 and out.json()["levels"]["order_cancel"] == "auto"
    now = await levels(env)
    assert now["order_cancel"] == "auto" and now["refund_create"] == "approval"
    after = (await env.client.get("/policies", headers=env.wa.headers)).json()
    assert len(after) == before + 1
    assert sum(1 for p in after if p["status"] == "published") == 1
    tenant = await env.q("SELECT auto_enabled_by, auto_enabled_at FROM tenants")
    assert tenant[0].auto_enabled_by is not None and tenant[0].auto_enabled_at is not None
    audit = await env.q("SELECT reason FROM audit_log WHERE action = 'admin.autonomy_changed'")
    assert [a.reason for a in audit] == ["pilot ke baad approve"]


async def test_owner_cannot_call_the_admin_route(env: AgentEnv) -> None:
    # The admin route itself still exists (platform support use), but a brand
    # owner must use their own `/policies/autonomy` route, not this one.
    await setup(env, "approval", mode="draft")
    denied = await env.client.put(
        f"/admin/tenants/{env.wa.tenant_id}/autonomy",
        json={"tool": "order_cancel", "level": "auto", "reason": "owner try kar raha"},
        headers=env.wa.headers,
    )
    assert denied.status_code == 403


async def test_turning_the_last_auto_tool_down_clears_the_marker(env: AgentEnv) -> None:
    await setup(env, "approval", mode="auto")
    admin = await admin_headers(env)
    url = f"/admin/tenants/{env.wa.tenant_id}/autonomy"
    await env.client.put(
        url,
        json={"tool": "order_cancel", "level": "auto", "reason": "pilot approve"},
        headers=admin,
    )
    await env.client.put(
        url,
        json={"tool": "order_cancel", "level": "approval", "reason": "rollback kiya"},
        headers=admin,
    )
    assert (await levels(env))["order_cancel"] == "approval"
    tenant = await env.q("SELECT auto_enabled_by FROM tenants")
    assert tenant[0].auto_enabled_by is None


async def test_owner_can_lower_but_not_raise_limits_under_auto(env: AgentEnv) -> None:
    await setup(env, "approval", mode="auto")
    admin = await admin_headers(env)
    await env.client.put(
        f"/admin/tenants/{env.wa.tenant_id}/autonomy",
        json={"tool": "order_cancel", "level": "auto", "reason": "pilot approve"},
        headers=admin,
    )
    published = next(
        p
        for p in (await env.client.get("/policies", headers=env.wa.headers)).json()
        if p["status"] == "published"
    )
    body = published["content"]
    lower = {**body, "limits": {**body["limits"], "cancel_auto_limit_paise": 100_000}}
    ok = await env.client.post("/policies", json={"content": lower}, headers=env.wa.headers)
    assert ok.status_code == 201
    higher = {**body, "limits": {**body["limits"], "cancel_auto_limit_paise": 900_000}}
    bad = await env.client.post("/policies", json={"content": higher}, headers=env.wa.headers)
    assert bad.status_code == 422 and error_code(bad) == "E-POL-002"
    assert v2_policy().autonomy.order_cancel == "auto"


async def test_approval_detail_timeline_and_sla(env: AgentEnv) -> None:
    from tests.v1.flow import plan, say

    await setup(env, "approval", mode="auto")
    await say(env, "order #1001 cancel kar do", "t1", plan())
    await say(env, "haan", "t2")
    listed = (
        await env.client.get("/approvals?status=pending_approval", headers=env.wa.headers)
    ).json()
    assert listed[0]["overdue"] is False and listed[0]["sla_due_at"] is not None
    detail = await env.client.get(f"/approvals/{listed[0]['id']}", headers=env.wa.headers)
    actions = [e["action"] for e in detail.json()["timeline"]]
    assert actions[:2] == ["action.proposed", "action.customer_confirmed"]
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE agent_actions SET created_at = :t WHERE status = 'pending_approval'"),
            {"t": timeutil.utc_now() - timedelta(hours=3)},
        )
    late = (
        await env.client.get("/approvals?status=pending_approval", headers=env.wa.headers)
    ).json()
    assert late[0]["overdue"] is True
    assert (await ops.approval_sla(ctx(env, "approval_sla")))["overdue"] == 1
    again = await ops.approval_sla(ctx(env, "approval_sla"))
    assert again["overdue"] == 1
    notes = await env.q("SELECT 1 FROM notifications WHERE title = 'Approvals ruki hui hain'")
    assert len(notes) == 1
