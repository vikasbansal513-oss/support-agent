from datetime import timedelta

from sqlalchemy import text

from app.core import timeutil
from app.core.db import session_scope
from app.core.monitoring import scrub_event
from app.jobs import ops
from app.worker import schedule_periodic
from tests.agent.helpers import AgentEnv, cls
from tests.auth.helpers import bearer
from tests.auth.test_admin_flow import _admin_login_with_mfa
from tests.channels.helpers import wa_message
from tests.ops.test_privacy import ctx, seed


async def notifications(env: AgentEnv, title: str) -> int:
    rows = await env.q("SELECT count(*) AS n FROM notifications WHERE title = :t", t=title)
    return int(rows[0].n)


async def test_sla_check_realerts_with_cooldown_and_audit(env: AgentEnv) -> None:
    await env.publish_policy()
    env.provider.responses = [cls(intent="refund", ref=None)]
    await env.set_mode("auto")
    await env.client.post(
        env.wa.path, json=wa_message("h1", body="mujhe kisi insaan se baat karni hai")
    )
    await env.drain()
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE handoffs SET sla_due_at = now() - interval '1 hour', would_handoff = false"
            )
        )
    first = await ops.sla_check(ctx(env, "sla_check"))
    again = await ops.sla_check(ctx(env, "sla_check"))
    assert first["alerted"] >= 1 and again["alerted"] == 0
    rows = await env.q("SELECT alert_count FROM handoffs")
    assert rows[0].alert_count == 1
    assert await env.q("SELECT 1 FROM audit_log WHERE action = 'handoff.sla_realert'")
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE handoffs SET last_alert_at = now() - interval '31 minutes'")
        )
    assert (await ops.sla_check(ctx(env, "sla_check")))["alerted"] >= 1


async def test_llm_latency_alert_only_when_slow_and_enough_calls(env: AgentEnv) -> None:
    async def insert(latency: int, n: int) -> None:
        async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
            for _ in range(n):
                await session.execute(
                    text(
                        "INSERT INTO llm_calls (purpose, provider, model, latency_ms, outcome)"
                        " VALUES ('classify', 'anthropic', 'm', :l, 'ok')"
                    ),
                    {"l": latency},
                )

    await insert(9000, 3)
    assert (await ops.llm_latency_check(ctx(env, "llm_latency_check")))["alert"] is False
    await insert(9000, 5)
    result = await ops.llm_latency_check(ctx(env, "llm_latency_check"))
    assert result["alert"] is True and result["p95_ms"] == 9000
    await ops.llm_latency_check(ctx(env, "llm_latency_check"))
    assert await notifications(env, "LLM dheere chal raha hai") == 1


def test_p95_nearest_rank() -> None:
    assert ops.p95(list(range(1, 101))) == 95
    assert ops.p95([7]) == 7


async def test_unanswered_alert_counts_stuck_inbound(env: AgentEnv) -> None:
    assert (await ops.unanswered_alert(ctx(env, "unanswered_alert")))["unanswered"] == 0
    async with session_scope(env.owner_factory, super_admin=True) as session:
        await session.execute(
            text(
                "INSERT INTO jobs (tenant_id, kind, status, created_at)"
                " VALUES (:t, 'process_inbound', 'deferred', :c)"
            ),
            {"t": env.wa.tenant_id, "c": timeutil.utc_now() - timedelta(minutes=30)},
        )
    assert (await ops.unanswered_alert(ctx(env, "unanswered_alert")))["unanswered"] == 1
    assert await notifications(env, "Kuch customers ko jawab nahi gaya") == 1
    async with session_scope(env.owner_factory, super_admin=True) as session:
        await session.execute(
            text("DELETE FROM jobs WHERE kind = 'process_inbound' AND status = 'deferred'")
        )


async def test_privacy_due_warns_near_deadline(env: AgentEnv) -> None:
    await env.client.post(
        "/privacy/requests",
        json={"subject_ref": "+919876543210", "kind": "export"},
        headers=env.wa.headers,
    )
    assert (await ops.privacy_due(ctx(env, "privacy_due")))["due"] == 0
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE privacy_requests SET due_at = now() + interval '2 days'")
        )
    assert (await ops.privacy_due(ctx(env, "privacy_due")))["due"] == 1


async def test_scheduler_creates_only_needed_ops_jobs_once_per_bucket(env: AgentEnv) -> None:
    await seed(env)
    await env.client.post(
        "/privacy/requests",
        json={"subject_ref": "+919876543210", "kind": "export"},
        headers=env.wa.headers,
    )
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(text("DELETE FROM jobs WHERE kind <> 'process_inbound'"))
    now = timeutil.utc_now()
    first = await schedule_periodic(env.factory, now)
    assert first >= 4
    assert await schedule_periodic(env.factory, now) == 0
    kinds = {r.kind for r in await env.q("SELECT kind FROM jobs")}
    assert {"llm_latency_check", "unanswered_alert", "privacy_due", "retention"} <= kinds
    assert "sla_check" not in kinds


async def test_global_pause_stops_agent_and_resume_restores(env: AgentEnv) -> None:
    await env.publish_policy()
    await env.set_mode("auto")
    _, verified, _ = await _admin_login_with_mfa(env.client, env.factory)
    admin = bearer(verified)
    paused = await env.client.post(
        "/admin/global-pause", json={"reason": "incident test"}, headers=admin
    )
    assert paused.status_code == 200 and paused.json() == {"status": "paused"}
    env.provider.responses = [cls()]
    await env.client.post(env.wa.path, json=wa_message("gp1", body="mera order #1001 kahan hai"))
    await env.drain()
    assert env.sender.sent == []
    state = await env.client.get("/admin/global-pause", headers=admin)
    assert state.json() == {"status": "paused"}
    resumed = await env.client.post(
        "/admin/global-resume", json={"reason": "all clear"}, headers=admin
    )
    assert resumed.json() == {"status": "running"}
    env.provider.responses = [cls()]
    await env.client.post(env.wa.path, json=wa_message("gp2", body="mera order #1001 kahan hai"))
    await env.drain()
    assert len(env.sender.sent) >= 1


async def test_global_pause_admin_only(env: AgentEnv) -> None:
    denied = await env.client.post(
        "/admin/global-pause", json={"reason": "not allowed"}, headers=env.wa.headers
    )
    assert denied.status_code in (401, 403)


def test_sentry_scrub_removes_pii() -> None:
    event = {
        "message": "failed for +919876543210",
        "request": {"data": "secret body", "cookies": "x", "headers": {"a": "b"}, "url": "/x"},
        "user": {"email": "a@b.com"},
        "exception": {
            "values": [
                {
                    "value": "bad a@b.com",
                    "stacktrace": {"frames": [{"vars": {"token": "t"}, "lineno": 1}]},
                }
            ]
        },
    }
    scrubbed = scrub_event(event)
    assert scrubbed is not None
    assert "request" in scrubbed and set(scrubbed["request"]) == {"url"}
    assert "user" not in scrubbed
    assert "+919876543210" not in scrubbed["message"]
    value = scrubbed["exception"]["values"][0]
    assert "a@b.com" not in value["value"]
    assert "vars" not in value["stacktrace"]["frames"][0]
