import uuid

from sqlalchemy import text

from app.auth.tokens import create_access_token
from app.core.db import session_scope
from tests.agent.helpers import AgentEnv
from tests.auth.helpers import error_code
from tests.v1.flow import setup


async def agent_headers(env: AgentEnv) -> dict[str, str]:
    """A staff/agent-role user on the same tenant as env.wa (the owner)."""
    user_id = uuid.uuid4()
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("INSERT INTO users (id, email, name) VALUES (:i, :e, 'Agent One')"),
            {"i": user_id, "e": f"agent-{user_id.hex[:8]}@example.com"},
        )
        await session.execute(
            text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'agent')"),
            {"t": env.wa.tenant_id, "u": user_id},
        )
    token, _ = create_access_token(
        user_id=user_id, realm="brand", role="agent", tenant_id=env.wa.tenant_id, mfa_ok=True
    )
    return {"Authorization": f"Bearer {token}"}


async def levels(env: AgentEnv) -> dict[str, str]:
    listed = (await env.client.get("/policies", headers=env.wa.headers)).json()
    published = next(p for p in listed if p["status"] == "published")
    result: dict[str, str] = published["content"]["autonomy"]
    return result


async def test_owner_turns_on_auto_for_own_tenant(env: AgentEnv) -> None:
    await setup(env, "approval", mode="auto")
    out = await env.client.put(
        "/policies/autonomy",
        json={
            "tool": "order_cancel",
            "level": "auto",
            "reason": "pilot theek chala",
            "confirmed": True,
        },
        headers=env.wa.headers,
    )
    assert out.status_code == 200 and out.json()["levels"]["order_cancel"] == "auto"
    assert (await levels(env))["order_cancel"] == "auto"
    audit = await env.q(
        "SELECT actor_role FROM audit_log WHERE action = 'admin.autonomy_changed' ORDER BY occurred_at DESC LIMIT 1"
    )
    assert audit[0].actor_role == "owner"


async def test_owner_without_confirm_is_rejected(env: AgentEnv) -> None:
    await setup(env, "approval", mode="auto")
    out = await env.client.put(
        "/policies/autonomy",
        json={"tool": "order_cancel", "level": "auto", "reason": "pilot theek chala"},
        headers=env.wa.headers,
    )
    assert out.status_code == 422 and error_code(out) == "E-POL-004"
    assert (await levels(env))["order_cancel"] == "approval"


async def test_staff_role_cannot_set_autonomy(env: AgentEnv) -> None:
    await setup(env, "approval", mode="auto")
    staff = await agent_headers(env)
    out = await env.client.put(
        "/policies/autonomy",
        json={
            "tool": "order_cancel",
            "level": "auto",
            "reason": "staff try kar raha",
            "confirmed": True,
        },
        headers=staff,
    )
    assert out.status_code == 403
    assert (await levels(env))["order_cancel"] == "approval"


async def test_owner_can_turn_on_auto_regardless_of_tenant_mode(env: AgentEnv) -> None:
    # No admin step required: the owner decides off/approval/auto for their own
    # tenant on their own, whatever the tenant's overall (shadow/draft/auto) mode is.
    await setup(env, "approval", mode="draft")
    out = await env.client.put(
        "/policies/autonomy",
        json={
            "tool": "order_cancel",
            "level": "auto",
            "reason": "jaldi chalu karna hai",
            "confirmed": True,
        },
        headers=env.wa.headers,
    )
    assert out.status_code == 200 and out.json()["levels"]["order_cancel"] == "auto"
    assert (await levels(env))["order_cancel"] == "auto"
