import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.actions import engine
from app.core.db import session_scope
from app.core.errors import AppError
from app.integrations.gupshup import FakeGupshupSender
from tests.agent.helpers import AgentEnv, cls
from tests.channels.helpers import wa_message


async def conversation(env: AgentEnv) -> uuid.UUID:
    env.provider.responses = [cls()]
    await env.client.post(env.wa.path, json=wa_message("e0", body="hi"))
    await env.drain()
    return uuid.UUID(str((await env.q("SELECT id FROM conversations"))[0].id))


async def submit(
    env: AgentEnv, conv: uuid.UUID, key: str, mode: str = "auto", body: str = "hello"
) -> engine.ActionResult:
    return await engine.submit_reply(
        env.factory,
        env.senders(),
        tenant_id=env.wa.tenant_id,
        conversation_id=conv,
        run_id=None,
        body=body,
        key=key,
        mode=mode,
    )


async def test_same_key_twice_sends_once(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    first = await submit(env, conv, "k1")
    second = await submit(env, conv, "k1")
    assert first.status == "sent" and second.already_handled
    assert len([s for s in env.sender.sent if s[1] == "hello"]) == 1


async def test_two_concurrent_workers_one_send(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    results = await asyncio.gather(*(submit(env, conv, "race") for _ in range(4)))
    assert len([s for s in env.sender.sent if s[1] == "hello"]) == 1
    assert sorted(r.status for r in results).count("sent") == 1


async def test_opt_out_suppresses(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO opt_outs (customer_id, channel, source)"
                " SELECT customer_id, 'whatsapp', 'customer_stop' FROM conversations"
            )
        )
    result = await submit(env, conv, "oo")
    assert (result.status, result.detail) == ("suppressed", "OPTED_OUT")
    assert not [s for s in env.sender.sent if s[1] == "hello"]


async def test_closed_window_suppresses(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE conversations SET last_inbound_at = now() - interval '25 hours'")
        )
    result = await submit(env, conv, "w")
    assert (result.status, result.detail) == ("suppressed", "WINDOW_CLOSED")


async def test_paused_suppresses(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    await env.set_paused(True)
    result = await submit(env, conv, "pz")
    assert (result.status, result.detail) == ("suppressed", "AGENT_PAUSED")


async def test_customer_daily_cap(env: AgentEnv) -> None:
    await env.publish_policy(customer_daily_msg_cap=2)
    conv = await conversation(env)
    statuses = [(await submit(env, conv, f"cap{i}")).status for i in range(4)]
    assert statuses.count("suppressed") >= 1
    sent = [s for s in env.sender.sent if s[1] == "hello"]
    assert len(sent) <= 2


async def test_known_send_failure_marks_failed_and_can_be_reclaimed(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    env.sender.fail_with = "rejected"
    bad = await submit(env, conv, "f1")
    assert (bad.status, bad.detail) == ("failed", "rejected")
    env.sender.fail_with = None
    again = await submit(env, conv, "f1")
    assert again.status == "sent"
    assert len([s for s in env.sender.sent if s[1] == "hello"]) == 1
    row = await env.q("SELECT status, attempts FROM agent_actions WHERE idempotency_key = 'f1'")
    assert (row[0].status, row[0].attempts) == ("sent", 2)


async def test_unknown_send_outcome_keeps_claim_and_raises_deferred(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    env.sender.fail_with = "timeout"
    with pytest.raises(engine.ActionDeferred):
        await submit(env, conv, "u1")
    row = await env.q("SELECT status FROM agent_actions WHERE idempotency_key = 'u1'")
    assert row[0].status == "claimed"
    env.sender.fail_with = None
    retry = await submit(env, conv, "u1")
    assert retry.already_handled and not [s for s in env.sender.sent if s[1] == "hello"]


async def test_stale_claim_becomes_needs_review(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    env.sender.fail_with = "timeout"
    with pytest.raises(engine.ActionDeferred):
        await submit(env, conv, "st")
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_actions SET claimed_at = now() - interval '30 minutes'"
                " WHERE idempotency_key = 'st'"
            )
        )
    env.sender.fail_with = None
    result = await submit(env, conv, "st")
    assert (result.status, result.detail) == ("needs_review", "STALE_CLAIM")


async def test_draft_then_approve_sends_and_reject_blocks(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    user_id = (await env.q("SELECT user_id FROM memberships"))[0].user_id
    draft = await submit(env, conv, "dr1", mode="draft", body="draft body")
    assert draft.status == "draft" and not [s for s in env.sender.sent if s[1] == "draft body"]
    assert draft.action_id is not None
    done = await engine.approve(
        env.factory, env.senders(), env.wa.tenant_id, draft.action_id, user_id
    )
    assert done.status == "sent"
    assert [s for s in env.sender.sent if s[1] == "draft body"]
    with pytest.raises(AppError) as again:
        await engine.approve(env.factory, env.senders(), env.wa.tenant_id, draft.action_id, user_id)
    assert again.value.code == "E-ACT-001"
    other = await submit(env, conv, "dr2", mode="draft", body="nope")
    assert other.action_id is not None
    await engine.reject(env.factory, env.wa.tenant_id, other.action_id, user_id)
    with pytest.raises(AppError):
        await engine.approve(env.factory, env.senders(), env.wa.tenant_id, other.action_id, user_id)


async def test_approve_respects_window(env: AgentEnv) -> None:
    await env.publish_policy()
    conv = await conversation(env)
    user_id = (await env.q("SELECT user_id FROM memberships"))[0].user_id
    draft = await submit(env, conv, "dw", mode="draft", body="late")
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE conversations SET last_inbound_at = now() - interval '26 hours'")
        )
    assert draft.action_id is not None
    with pytest.raises(AppError) as err:
        await engine.approve(env.factory, env.senders(), env.wa.tenant_id, draft.action_id, user_id)
    assert err.value.code == "E-ACT-002"


def test_fake_sender_is_importable() -> None:
    assert FakeGupshupSender().sent == []
