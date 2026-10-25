import asyncio
import uuid

from sqlalchemy import text

from app.actions import engine
from app.core.db import session_scope
from tests.agent.helpers import AgentEnv, cls
from tests.channels.helpers import wa_message


async def conversation(env: AgentEnv) -> uuid.UUID:
    env.provider.responses = [cls()]
    await env.client.post(env.wa.path, json=wa_message("s0", body="hi"))
    await env.drain()
    return uuid.UUID(str((await env.q("SELECT id FROM conversations"))[0].id))


async def submit(env: AgentEnv, conv: uuid.UUID, key: str) -> engine.ActionResult:
    return await engine.submit_reply(
        env.factory,
        env.senders(),
        tenant_id=env.wa.tenant_id,
        conversation_id=conv,
        run_id=None,
        body="hello",
        key=key,
        mode="auto",
    )


async def test_tampered_recipient_is_suppressed(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    env.sender.fail_with = "rejected"
    bad = await submit(env, conv, "tamper")
    assert bad.status == "failed"
    env.sender.fail_with = None
    async with session_scope(env.owner_factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_actions SET recipient = '+919811111111' WHERE idempotency_key = 'tamper'"
            )
        )
    sent_before = len(env.sender.sent)
    result = await submit(env, conv, "tamper")
    assert result.status == "suppressed" and result.detail == "RECIPIENT_MISMATCH"
    assert len(env.sender.sent) == sent_before


async def test_same_key_and_concurrency_send_once(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    results = await asyncio.gather(*(submit(env, conv, "dup") for _ in range(5)))
    assert len([s for s in env.sender.sent if s[1] == "hello"]) == 1
    assert [r.status for r in results].count("sent") == 1


async def test_duplicate_webhook_single_message(env: AgentEnv) -> None:
    env.provider.responses = [cls()]
    payload = wa_message("same", body="hi")
    await env.client.post(env.wa.path, json=payload)
    await env.client.post(env.wa.path, json=payload)
    await env.drain()
    rows = await env.q("SELECT count(*) AS n FROM messages WHERE direction = 'in'")
    assert rows[0].n == 1


async def test_wrong_webhook_secret_is_404(env: AgentEnv) -> None:
    response = await env.client.post("/webhooks/whatsapp/" + "y" * 43, json=wa_message("w"))
    assert response.status_code == 404
    assert await env.q("SELECT 1 FROM inbound_events") == []
