import json
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


async def test_combined_order_status_and_policy_question_merges_facts_into_one_reply(
    env: AgentEnv,
) -> None:
    """Section 5 Phase 1: "order kaha hai + return policy kya hai" in one message
    now answers both in one reply instead of handing off the policy half. The
    planner names order_status as primary (it needs the order lookup) and lists
    policy_question in secondary_answers; v1/graph.py's answer() must then merge
    policy facts alongside the order facts it already gathers, and pass both to
    the reply composer in one LLM call - this is exercised directly against the
    real compose_reply/reply_layer.compose path (not the template fallback), by
    supplying a second fake LLM response for the reply-composer call."""
    await setup(env)
    # return_window_days isn't set in setup()'s policy body, so PolicyV2 falls
    # back to its default of 7 (app/v1/policy.py) - the reply text has to use
    # that same number, or validate_reply_v1's known-facts-only number check
    # rejects "unknown_number" (it's not in the facts dict), the compose()
    # retry burns the only other queued fake LLM response, and the graph falls
    # back to the plain template reply instead of this composed one.
    reply_json = json.dumps(
        {
            "text": (
                "Order:\n- Order: *#1001*\n- Status: *unfulfilled*\n\n"
                "Return policy:\n- Return window: *7 days*"
            ),
            "language": "hinglish",
            "facts_used": ["order_name", "status", "return_window_days"],
            "promises": [],
            "needs_human": False,
        }
    )
    env.provider.responses = [
        plan("order_status", "answer", None, secondary_answers=["policy_question"]),
        reply_json,
    ]
    await say(env, "order #1001 kaha hai aur return policy kya hai", "m1")
    assert "#1001" in last_reply(env) and "7 days" in last_reply(env)
    # Both the primary order fact and the secondary policy fact were actually
    # offered to the reply composer in the same call - not, say, two separate
    # LLM calls or a reply built from only one of the two groups.
    reply_call_user = env.provider.calls[-1][1]
    assert '"order_name"' in reply_call_user and '"return_window_days"' in reply_call_user
    calls = await env.q("SELECT purpose FROM llm_calls ORDER BY created_at")
    assert [c.purpose for c in calls] == ["plan", "reply"]


async def test_combined_tracking_secondary_enrichment_is_dropped_on_lookup_failure(
    env: AgentEnv,
) -> None:
    """A secondary tracking lookup failing (FakeV1Tools.tracking_lookup always
    returns not_found - see tests/v1/flow.py) must not turn a perfectly good
    order_status answer into a handoff: the enrichment is silently dropped and
    the primary answer still goes out, falling back to the template reply here
    because only one fake LLM response (the plan) is supplied."""
    tools, _ = await setup(env)
    result, gate = facts()
    # facts() always builds a ToolResult with a real order (never None) - this
    # assert exists only to narrow the type for mypy (ToolResult.order is
    # Optional on the model since a failed lookup carries no facts).
    assert result.order is not None
    # Give the order a tracking number so the secondary-tracking branch in
    # v1/graph.py's answer() is actually entered (it's a no-op otherwise) -
    # FakeV1Tools.tracking_lookup still always returns not_found regardless.
    with_tracking = result.order.model_copy(
        update={"tracking_numbers": ["AWB123456789"], "courier": "Delhivery"}
    )
    tools.orders["#1001"] = (result.model_copy(update={"order": with_tracking}), gate)
    await say(
        env,
        "order #1001 kaha hai aur tracking bhi bata do",
        "m2",
        plan("order_status", "answer", None, secondary_answers=["tracking"]),
    )
    assert "#1001" in last_reply(env)
    assert await actions(env) == []


async def test_auto_cancel_needs_customer_confirm_then_executes_once(env: AgentEnv) -> None:
    tools, shop = await setup(env, "auto")
    await say(env, "order #1001 cancel kar do", "c1", plan())
    assert "cancel order" in last_reply(env) and "Rs 400" in last_reply(env)
    (pending,) = await actions(env, "order_cancel")
    assert pending.status == "awaiting_customer" and shop.calls == []
    await say(env, "haan", "c2")
    assert "has been cancelled" in last_reply(env)
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
    assert "has been cancelled" in last_reply(env)


async def test_customer_says_no_nothing_happens(env: AgentEnv) -> None:
    _, shop = await setup(env)
    await say(env, "order #1001 cancel kar do", "n1", plan())
    await say(env, "nahi rehne do", "n2")
    assert "no changes have been made" in last_reply(env)
    (action,) = await actions(env, "order_cancel")
    assert action.status == "rejected" and shop.calls == []


async def test_two_unclear_answers_hand_off(env: AgentEnv) -> None:
    _, shop = await setup(env)
    await say(env, "order #1001 cancel kar do", "u1", plan())
    await say(env, "pata nahi kya karun", "u2")
    assert "'yes' or 'no'" in last_reply(env)
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
    assert "has been updated" in last_reply(env)
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
    assert "has expired" in last_reply(env) and shop.calls == []


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


async def test_customer_mismatch_on_write_is_identity_mismatch(env: AgentEnv) -> None:
    tools, shop = await setup(env)
    tools.errors["#1001"] = "customer_mismatch"
    await say(env, "order #1001 cancel kar do", "k1", plan())
    assert (await env.q("SELECT reason_code FROM handoffs"))[0].reason_code == "IDENTITY_MISMATCH"
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
    assert "has been cancelled" in last_reply(env)
    again = await ops.write_upkeep(ctx(env, "write_upkeep"))
    assert again["resumed"] == 0 and len(shop.calls) == 1
