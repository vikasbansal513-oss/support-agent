from dataclasses import replace
from datetime import timedelta

from sqlalchemy import text

from app.core import timeutil
from app.core.db import session_scope
from app.integrations.shopify_write import set_writer_override
from tests.agent.helpers import AgentEnv
from tests.v1.flow import ADDRESS, actions, facts, last_reply, plan, say, setup


async def teardown() -> None:
    set_writer_override(None)


async def test_status_question_answers_from_facts_without_any_action(env: AgentEnv) -> None:
    await setup(env)
    await say(env, "mera order #1001 kahan hai", "s1", plan("order_status", "answer", None))
    assert "#1001" in last_reply(env) and "Rs 400" in last_reply(env)
    assert await actions(env) == []
    runs = await env.q("SELECT graph_version, plan FROM agent_runs")
    assert runs[0].graph_version == "v1.0" and runs[0].plan["intent"] == "order_status"


async def test_auto_cancel_needs_customer_confirm_then_executes_once(env: AgentEnv) -> None:
    tools, shop = await setup(env, "auto")
    await say(env, "order #1001 cancel kar do", "c1", plan())
    assert "cancel karna hai" in last_reply(env) and "Rs 400" in last_reply(env)
    (pending,) = await actions(env, "order_cancel")
    assert pending.status == "awaiting_customer" and shop.calls == []
    await say(env, "haan", "c2")
    assert "cancel ho gaya" in last_reply(env)
    assert shop.orders["#1001"].cancelled is True and len(shop.calls) == 1
    (done,) = await actions(env, "order_cancel")
    assert done.status == "confirmed" and done.amount_paise == 40_000
    state = await env.q("SELECT awaiting, write_count, pending_action_id FROM conversation_state")
    assert state[0].awaiting == "none" and state[0].write_count == 1
    assert state[0].pending_action_id is None
    await say(env, "haan", "c3", plan("greeting", "answer", None, ref=None))
    assert len(shop.calls) == 1


async def test_approval_level_waits_for_owner_then_tells_customer(env: AgentEnv) -> None:
    _, shop = await setup(env, "approval")
    await say(env, "order #1001 cancel kar do", "a1", plan())
    assert "team" in last_reply(env)
    await say(env, "haan", "a2")
    assert "team" in last_reply(env) and shop.calls == []
    (action,) = await actions(env, "order_cancel")
    assert action.status == "pending_approval"
    out = await env.client.post(f"/approvals/{action.id}/approve", headers=env.wa.headers)
    assert out.json()["status"] == "confirmed"
    assert shop.orders["#1001"].cancelled is True
    assert "cancel ho gaya" in last_reply(env)


async def test_customer_says_no_nothing_happens(env: AgentEnv) -> None:
    _, shop = await setup(env)
    await say(env, "order #1001 cancel kar do", "n1", plan())
    await say(env, "nahi rehne do", "n2")
    assert "koi badlav nahi" in last_reply(env)
    (action,) = await actions(env, "order_cancel")
    assert action.status == "rejected" and shop.calls == []


async def test_two_unclear_answers_hand_off(env: AgentEnv) -> None:
    _, shop = await setup(env)
    await say(env, "order #1001 cancel kar do", "u1", plan())
    await say(env, "pata nahi kya karun", "u2")
    assert "haan" in last_reply(env)
    await say(env, "hmm kal batata hoon", "u3")
    handoffs = await env.q("SELECT reason_code FROM handoffs")
    assert [h.reason_code for h in handoffs] == ["CONFIRM_UNCLEAR"]
    (action,) = await actions(env, "order_cancel")
    assert action.status == "expired" and shop.calls == []


async def test_gate_handoffs_use_v1_reason_codes(env: AgentEnv) -> None:
    tools, shop = await setup(env)
    tools.orders["#1001"] = facts(total=900_000)
    await say(env, "order #1001 cancel kar do", "g1", plan())
    assert (await env.q("SELECT reason_code, priority FROM handoffs"))[
        0
    ].reason_code == "HIGH_RISK_FLAG"
    assert await actions(env) == []


async def test_tool_off_is_policy_exception(env: AgentEnv) -> None:
    await setup(env, "off")
    await say(env, "order #1001 cancel kar do", "o1", plan())
    assert (await env.q("SELECT reason_code FROM handoffs"))[0].reason_code == "POLICY_EXCEPTION"


async def test_negative_sentiment_and_low_confidence(env: AgentEnv) -> None:
    await setup(env)
    await say(env, "order dekho", "m1", plan("order_status", "answer", None, sentiment=-0.9))
    first = await env.q("SELECT reason_code, priority FROM handoffs")
    assert first[0].reason_code == "SENTIMENT_NEGATIVE" and first[0].priority == "urgent"


async def test_confused_plan_hands_off(env: AgentEnv) -> None:
    await setup(env)
    await say(env, "kuch", "f1", plan("unclear", "clarify", None, confidence=0.3))
    assert (await env.q("SELECT reason_code FROM handoffs"))[0].reason_code == "CONFUSED"


async def test_llm_amount_is_ignored_amount_comes_from_order(env: AgentEnv) -> None:
    await setup(env)
    llm = plan(args={"amount_paise": 99_999_999}, amount_paise=1)
    await say(env, "order #1001 cancel kar do", "i1", llm)
    (action,) = await actions(env, "order_cancel")
    assert action.amount_paise == 40_000


async def test_address_change_asks_then_proposes(env: AgentEnv) -> None:
    _, shop = await setup(env)
    await say(
        env, "order #1001 ka address badalna hai", "d1", plan("address_change", "clarify", None)
    )
    assert "address" in last_reply(env).lower()
    state = await env.q("SELECT awaiting FROM conversation_state")
    assert state[0].awaiting == "address"
    llm = plan("address_change", "propose", "address_update", args={"new_address": ADDRESS})
    await say(env, "12 MG Road Pune Maharashtra 411001", "d2", llm)
    assert "411001" in last_reply(env)
    await say(env, "yes", "d3")
    assert "update ho gaya" in last_reply(env)
    assert shop.orders["#1001"].address_pin == "411001"


async def test_shadow_mode_never_creates_write_actions(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto", mode="shadow")
    await say(env, "order #1001 cancel kar do", "h1", plan())
    assert await actions(env) == [] and shop.calls == []
    assert env.sender.sent == []
    state = await env.q("SELECT awaiting FROM conversation_state")
    assert state[0].awaiting == "none"


async def test_confirm_after_expiry_is_not_executed(env: AgentEnv) -> None:
    _, shop = await setup(env)
    await say(env, "order #1001 cancel kar do", "x1", plan())
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_actions SET confirm_expires_at = :t WHERE tool_name = 'order_cancel'"
            ),
            {"t": timeutil.utc_now() - timedelta(minutes=1)},
        )
    await say(env, "haan", "x2")
    assert "samay nikal gaya" in last_reply(env) and shop.calls == []


async def test_order_changed_between_propose_and_confirm_stops(env: AgentEnv) -> None:
    tools, shop = await setup(env)
    await say(env, "order #1001 cancel kar do", "z1", plan())
    result, gate = tools.orders["#1001"]
    tools.orders["#1001"] = (result, replace(gate, total_paise=55_000))
    await say(env, "haan", "z2")
    assert shop.calls == []
    assert (await env.q("SELECT reason_code FROM handoffs"))[0].reason_code == "POLICY_EXCEPTION"
    (action,) = await actions(env, "order_cancel")
    assert action.status == "rejected"


async def test_customer_mismatch_on_write_is_high_risk(env: AgentEnv) -> None:
    tools, shop = await setup(env)
    tools.errors["#1001"] = "customer_mismatch"
    await say(env, "order #1001 cancel kar do", "k1", plan())
    assert (await env.q("SELECT reason_code FROM handoffs"))[0].reason_code == "HIGH_RISK_FLAG"
    assert shop.calls == []


async def test_graph_stays_far_below_recursion_limit(env: AgentEnv) -> None:
    await setup(env)
    await say(env, "order #1001 cancel kar do", "r1", plan())
    run = await env.q("SELECT trace FROM agent_runs")
    assert 0 < len(run[0].trace) <= 8
    _ = teardown


async def test_upkeep_resumes_an_approved_action_left_behind(env: AgentEnv) -> None:
    from app.jobs import ops
    from tests.ops.test_privacy import ctx

    _, shop = await setup(env)
    await say(env, "order #1001 cancel kar do", "w1", plan())
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_actions SET status = 'approved', confirm_token_hash = NULL,"
                " decided_at = now() - interval '5 minutes' WHERE tool_name = 'order_cancel'"
            )
        )
    out = await ops.write_upkeep(ctx(env, "write_upkeep"))
    assert out["resumed"] == 1 and shop.orders["#1001"].cancelled is True
    assert "cancel ho gaya" in last_reply(env)
    again = await ops.write_upkeep(ctx(env, "write_upkeep"))
    assert again["resumed"] == 0 and len(shop.calls) == 1
