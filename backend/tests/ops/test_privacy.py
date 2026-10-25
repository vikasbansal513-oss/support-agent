import uuid

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope
from app.jobs import ops
from app.jobs.handlers import JobContext
from app.jobs.queue import Job
from app.privacy import service as privacy
from tests.agent.helpers import AgentEnv, cls
from tests.auth.helpers import error_code
from tests.channels.helpers import wa_message

PHONE = "+919876543210"


async def seed(env: AgentEnv, mid: str = "p1", body: str = "mera order #1001 kahan hai") -> None:
    env.provider.responses = [cls()]
    await env.client.post(env.wa.path, json=wa_message(mid, body=body))
    await env.drain()


def ctx(env: AgentEnv, kind: str) -> JobContext:
    job = Job(
        id=uuid.uuid4(),
        tenant_id=env.wa.tenant_id,
        kind=kind,
        payload={},
        attempts=1,
        max_attempts=1,
    )
    return JobContext(factory=env.factory, job=job)


async def test_consent_recorded_on_first_message_and_opt_out(env: AgentEnv) -> None:
    await seed(env)
    rows = await env.q("SELECT purpose, granted FROM consents ORDER BY purpose")
    assert {(r.purpose, r.granted) for r in rows} == {
        ("ai_processing", True),
        ("whatsapp_optin", True),
    }
    await env.client.post(env.wa.path, json=wa_message("p2", body="STOP"))
    await env.drain()
    latest = await env.q(
        "SELECT granted FROM consents WHERE purpose = 'whatsapp_optin' ORDER BY created_at DESC"
    )
    assert latest[0].granted is False and len(latest) == 2


async def test_consents_are_append_only(env: AgentEnv) -> None:
    await seed(env)
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        try:
            await session.execute(text("UPDATE consents SET granted = false"))
        except Exception as exc:
            assert "permission denied" in str(exc) or "append" in str(exc)
        else:
            raise AssertionError("update must fail")


async def test_request_create_list_duplicate_409(env: AgentEnv) -> None:
    body = {"subject_ref": "98765 43210", "kind": "export"}
    first = await env.client.post("/privacy/requests", json=body, headers=env.wa.headers)
    assert first.status_code == 201 and first.json()["status"] == "open"
    second = await env.client.post("/privacy/requests", json=body, headers=env.wa.headers)
    assert second.status_code == 409 and error_code(second) == "E-PRV-001"
    other = await env.client.post(
        "/privacy/requests", json={**body, "kind": "delete"}, headers=env.wa.headers
    )
    assert other.status_code == 201
    listed = await env.client.get("/privacy/requests", headers=env.wa.headers)
    assert len(listed.json()) == 2


async def test_bad_subject_rejected(env: AgentEnv) -> None:
    bad = await env.client.post(
        "/privacy/requests", json={"subject_ref": "abc", "kind": "export"}, headers=env.wa.headers
    )
    assert bad.status_code in (400, 422)


async def test_export_contains_messages_and_closes_request(env: AgentEnv) -> None:
    await seed(env)
    await env.client.post(
        "/privacy/requests", json={"subject_ref": PHONE, "kind": "export"}, headers=env.wa.headers
    )
    response = await env.client.get(
        "/privacy/export", params={"subject_ref": PHONE}, headers=env.wa.headers
    )
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    contents = [m["content"] for c in data["conversations"] for m in c["messages"]]
    assert "mera order #1001 kahan hai" in contents
    assert {c["purpose"] for c in data["consents"]} >= {"ai_processing"}
    status = await env.q("SELECT status FROM privacy_requests")
    assert status[0].status == "done"


async def test_export_unknown_subject_is_empty(env: AgentEnv) -> None:
    response = await env.client.get(
        "/privacy/export", params={"subject_ref": "+919811112222"}, headers=env.wa.headers
    )
    assert response.status_code == 200 and response.json()["conversations"] == []


async def test_delete_request_erases_everything(env: AgentEnv) -> None:
    await env.publish_policy()
    await seed(env)
    await env.client.post(
        "/privacy/requests", json={"subject_ref": PHONE, "kind": "delete"}, headers=env.wa.headers
    )
    result = await ops.retention(ctx(env, "retention"))
    assert result["privacy_deleted"] == 1
    for table in (
        "customers",
        "conversations",
        "messages",
        "agent_runs",
        "agent_actions",
        "inbound_events",
    ):
        rows = await env.q(f"SELECT count(*) AS n FROM {table}")  # noqa: S608
        assert rows[0].n == 0, table
    done = await env.q("SELECT status FROM privacy_requests")
    assert done[0].status == "done"
    audit = await env.q("SELECT 1 FROM audit_log WHERE action = 'privacy.deleted'")
    assert audit


async def test_legal_hold_blocks_delete_and_retention(env: AgentEnv) -> None:
    await seed(env)
    async with session_scope(env.owner_factory, super_admin=True) as session:
        await session.execute(
            text("UPDATE tenants SET legal_hold = true WHERE id = :t"), {"t": env.wa.tenant_id}
        )
        await session.execute(
            text("UPDATE conversations SET last_message_at = now() - interval '90 days'")
        )
    await env.client.post(
        "/privacy/requests", json={"subject_ref": PHONE, "kind": "delete"}, headers=env.wa.headers
    )
    result = await ops.retention(ctx(env, "retention"))
    assert result["privacy_deleted"] == 0 and result["conversations"] == 0
    assert (await env.q("SELECT count(*) AS n FROM customers"))[0].n == 1


async def test_retention_deletes_old_conversations_only(env: AgentEnv) -> None:
    await seed(env)
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE conversations SET last_message_at = now() - interval '31 days'")
        )
    result = await privacy.run_retention(env.factory, env.wa.tenant_id)
    assert result["conversations"] == 1
    assert (await env.q("SELECT count(*) AS n FROM messages"))[0].n == 0
    audit = await env.q("SELECT 1 FROM audit_log WHERE action = 'retention.conversations_deleted'")
    assert audit

    await seed(env, "p9")
    kept = await privacy.run_retention(env.factory, env.wa.tenant_id)
    assert kept["conversations"] == 0
    assert get_settings().retention_days == 30


async def test_public_notices_draft_banner(env: AgentEnv) -> None:
    for path in ("/public/privacy-notice", "/public/terms"):
        response = await env.client.get(path)
        body = response.json()
        assert response.status_code == 200 and body["draft"] is True
        assert "<h1>" in body["html"] and body["version"]


async def test_privacy_requires_owner(env: AgentEnv) -> None:
    assert (await env.client.get("/privacy/requests")).status_code in (401, 403)
