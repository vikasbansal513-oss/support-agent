import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.actions import writes
from app.core import timeutil
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError
from app.integrations.shopify_write import DisabledShopifyWriter, ShopifyWriter
from app.v1.gate import GateDecision
from app.v1.policy import PolicyV2
from tests.agent.helpers import AgentEnv
from tests.v1.helpers import Conv, decision, make_conv, policy, propose_and_confirm, shop

ADDRESS = {
    "new_address": {
        "line1": "12 MG Road",
        "city": "Pune",
        "state": "MH",
        "pin": "411001",
        "country": "IN",
    }
}


async def run(
    env: AgentEnv,
    fake: ShopifyWriter,
    action: writes.ActionRow,
    pol: PolicyV2,
    enforce: bool = True,
) -> writes.WriteOutcome:
    return await writes.execute(
        env.factory, fake, env.wa.tenant_id, action.id, pol, enforce_limits=enforce
    )


async def status_of(env: AgentEnv, action: writes.ActionRow) -> str:
    rows = await env.q("SELECT status FROM agent_actions WHERE id = :i", i=action.id)
    return str(rows[0].status)


async def test_auto_path_cancels_once_and_records_usage(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    action = await propose_and_confirm(env, conv, pol)
    assert action.status == "approved"
    out = await run(env, fake, action, pol)
    assert out.status == "confirmed"
    assert fake.orders["#1001"].cancelled is True
    assert len(fake.calls) == 1 and fake.calls[0]["key"] == action.key
    usage = await env.q("SELECT count, sum_paise FROM action_usage WHERE tool = 'order_cancel'")
    assert (usage[0].count, usage[0].sum_paise) == (1, 40_000)
    audit = await env.q(
        "SELECT action FROM audit_log WHERE entity_id = :i ORDER BY occurred_at", i=str(action.id)
    )
    assert [a.action for a in audit][:2] == ["action.proposed", "action.customer_confirmed"]
    assert "action.confirmed" in [a.action for a in audit]


async def test_second_execute_is_a_noop(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    action = await propose_and_confirm(env, conv, pol)
    await run(env, fake, action, pol)
    again = await run(env, fake, action, pol)
    assert again.detail == "ALREADY_HANDLED" and len(fake.calls) == 1


async def test_two_concurrent_executes_write_once(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    action = await propose_and_confirm(env, conv, pol)
    results = await asyncio.gather(*(run(env, fake, action, pol) for _ in range(4)))
    assert len(fake.calls) == 1
    assert [r.status for r in results].count("confirmed") == 1


async def test_duplicate_proposal_returns_existing(env: AgentEnv) -> None:
    conv, pol = await make_conv(env), policy()
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        first = await writes.propose(
            session,
            conversation_id=conv.conversation_id,
            run_id=None,
            tool="order_cancel",
            order_ref="#1001",
            amount_paise=1,
            args={},
            decision=decision(),
            policy=pol,
            write_count=0,
        )
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        second = await writes.propose(
            session,
            conversation_id=conv.conversation_id,
            run_id=None,
            tool="order_cancel",
            order_ref="#1001",
            amount_paise=1,
            args={},
            decision=decision(),
            policy=pol,
            write_count=0,
        )
    assert first.created and not second.created and second.action.id == first.action.id


async def test_only_one_active_write_per_conversation(env: AgentEnv) -> None:
    conv, pol = await make_conv(env), policy()
    await propose_and_confirm(env, conv, pol, n=0)
    with pytest.raises(IntegrityError):
        await propose_and_confirm(env, conv, pol, tool="refund_create", n=1)


async def test_replayed_and_wrong_confirm_rejected(env: AgentEnv) -> None:
    conv, pol = await make_conv(env), policy()
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        proposal = await writes.propose(
            session,
            conversation_id=conv.conversation_id,
            run_id=None,
            tool="order_cancel",
            order_ref="#1001",
            amount_paise=1,
            args={},
            decision=decision(),
            policy=pol,
            write_count=0,
        )
    for token in ("wrong-token", proposal.token):
        try:
            async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
                await writes.confirm(session, proposal.action.id, token)
        except AppError as exc:
            assert token == "wrong-token" and exc.code == "E-ACT-005"
    with pytest.raises(AppError) as replay:
        async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
            await writes.confirm(session, proposal.action.id, proposal.token)
    assert replay.value.code == "E-ACT-005"


async def test_expired_confirmation_is_refused_and_swept(env: AgentEnv) -> None:
    conv, pol = await make_conv(env), policy()
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        proposal = await writes.propose(
            session,
            conversation_id=conv.conversation_id,
            run_id=None,
            tool="order_cancel",
            order_ref="#1001",
            amount_paise=1,
            args={},
            decision=decision(),
            policy=pol,
            write_count=0,
        )
    late = timeutil.utc_now() + timedelta(minutes=31)
    with pytest.raises(AppError):
        async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
            await writes.confirm(session, proposal.action.id, proposal.token, now=late)
    async with session_scope(env.owner_factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE agent_actions SET confirm_expires_at = now() - interval '1 minute'")
        )
    assert await writes.expire_stale(env.factory, env.wa.tenant_id) == 1
    assert (await env.q("SELECT status FROM agent_actions"))[0].status == "expired"


async def test_approval_path_waits_then_executes_after_regate(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy("approval"), shop()
    action = await propose_and_confirm(env, conv, pol, route="approval")
    assert action.status == "pending_approval"
    assert fake.calls == []
    user = uuid.uuid4()

    async def ok(_: writes.ActionRow) -> GateDecision:
        return decision("approval", 40_000)

    out = await writes.approve(env.factory, fake, env.wa.tenant_id, action.id, user, pol, ok)
    assert out.status == "confirmed" and fake.orders["#1001"].cancelled
    row = (await env.q("SELECT approved_by, requires_approval FROM agent_actions"))[0]
    assert row.approved_by == user and row.requires_approval is True


async def test_approval_blocked_when_order_changed(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy("approval"), shop()
    action = await propose_and_confirm(env, conv, pol, route="approval")

    async def changed(_: writes.ActionRow) -> GateDecision:
        return GateDecision("handoff", "ALREADY_SHIPPED", "POLICY_EXCEPTION", 0, "T2")

    out = await writes.approve(
        env.factory, fake, env.wa.tenant_id, action.id, uuid.uuid4(), pol, changed
    )
    assert (out.status, out.detail) == ("rejected", "ALREADY_SHIPPED")
    assert fake.calls == []


async def test_reject_and_double_decision(env: AgentEnv) -> None:
    conv, pol = await make_conv(env), policy("approval")
    action = await propose_and_confirm(env, conv, pol, route="approval")
    await writes.reject(env.factory, env.wa.tenant_id, action.id, uuid.uuid4(), "not now")
    with pytest.raises(AppError):
        await writes.reject(env.factory, env.wa.tenant_id, action.id, uuid.uuid4(), "again")


async def test_timeout_after_apply_is_confirmed_by_reread(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    fake.script["order_cancel"] = ["timeout_applied"]
    action = await propose_and_confirm(env, conv, pol)
    assert (await run(env, fake, action, pol)).status == "confirmed"


async def test_timeout_not_applied_keeps_claim_for_review_then_reconciles(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    fake.script["order_cancel"] = ["timeout_not_applied"]
    action = await propose_and_confirm(env, conv, pol)
    out = await run(env, fake, action, pol)
    assert out.status == "needs_review"
    assert (
        await writes.reconcile(env.factory, fake, env.wa.tenant_id, action.id)
    ).detail == "STILL_UNVERIFIED"
    fake.orders["#1001"] = fake.orders["#1001"].__class__(
        **{**fake.orders["#1001"].__dict__, "cancelled": True}
    )
    assert (
        await writes.reconcile(env.factory, fake, env.wa.tenant_id, action.id)
    ).status == "confirmed"
    assert await status_of(env, action) == "confirmed"


async def test_rejected_write_fails_and_releases_usage(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    fake.script["order_cancel"] = ["rejected"]
    action = await propose_and_confirm(env, conv, pol)
    assert (await run(env, fake, action, pol)).status == "failed"
    usage = await env.q("SELECT count, sum_paise FROM action_usage")
    assert (usage[0].count, usage[0].sum_paise) == (0, 0)


async def test_upstream_error_is_needs_review_not_confirmed(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    fake.script["order_cancel"] = ["upstream_error"]
    action = await propose_and_confirm(env, conv, pol)
    assert (await run(env, fake, action, pol)).status == "needs_review"


async def test_partial_refund_never_reported_confirmed(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop(total=30_000)
    fake.script["refund_create"] = ["partial"]
    action = await propose_and_confirm(env, conv, pol, tool="refund_create", amount=30_000, args={})
    out = await run(env, fake, action, pol)
    assert out.status == "needs_review" and out.detail == "UNVERIFIED:ok"


async def test_unverified_writer_goes_to_needs_review(env: AgentEnv) -> None:
    conv, pol = await make_conv(env), policy()
    action = await propose_and_confirm(env, conv, pol)
    out = await run(env, DisabledShopifyWriter(), action, pol)
    assert (out.status, out.detail) == ("needs_review", "WRITE_UNAVAILABLE")
    usage = await env.q("SELECT count FROM action_usage")
    assert usage[0].count == 0


async def test_customer_daily_refund_limit_is_race_safe(env: AgentEnv) -> None:
    conv, fake = await make_conv(env), shop("#1", "#2", total=60_000)
    pol = policy(limits={"refund_auto_limit_paise": 60_000, "refund_customer_daily_paise": 100_000})
    first = await propose_and_confirm(
        env, conv, pol, tool="refund_create", order_ref="#1", amount=60_000, args={}, n=0
    )
    assert (await run(env, fake, first, pol)).status == "confirmed"
    second = await propose_and_confirm(
        env, conv, pol, tool="refund_create", order_ref="#2", amount=60_000, args={}, n=1
    )
    out = await run(env, fake, second, pol)
    assert (out.status, out.detail) == ("pending_approval", "LIMIT_EXCEEDED")
    assert fake.orders["#2"].refunded_paise == 0
    assert (
        await env.q(
            "SELECT requires_approval FROM agent_actions WHERE idempotency_key = :k", k=second.key
        )
    )[0].requires_approval


async def test_concurrent_auto_refunds_cannot_exceed_daily_limit(env: AgentEnv) -> None:
    conv1 = await make_conv(env, "+919800000051")
    fake = shop("#1", "#2", total=60_000)
    pol = policy(limits={"refund_auto_limit_paise": 60_000, "refund_customer_daily_paise": 100_000})
    a = await propose_and_confirm(
        env, conv1, pol, tool="refund_create", order_ref="#1", amount=60_000, args={}, n=0
    )
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        other = (
            await session.execute(
                text(
                    "INSERT INTO conversations (customer_id, channel) VALUES (:c, 'email') RETURNING id"
                ),
                {"c": conv1.customer_id},
            )
        ).scalar_one()
    conv_b = Conv(conv1.customer_id, uuid.UUID(str(other)))
    b = await propose_and_confirm(
        env, conv_b, pol, tool="refund_create", order_ref="#2", amount=60_000, args={}, n=0
    )
    results = await asyncio.gather(run(env, fake, a, pol), run(env, fake, b, pol))
    assert sorted(r.status for r in results) == ["confirmed", "pending_approval"]
    total = sum(o.refunded_paise for o in fake.orders.values())
    assert total == 60_000


async def test_kill_switch_blocks_execution_and_restore_allows(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    await writes.kill_tool(env.factory, env.wa.tenant_id, "order_cancel", uuid.uuid4(), "incident")
    action = await propose_and_confirm(env, conv, pol)
    out = await run(env, fake, action, pol)
    assert (out.status, out.detail) == ("rejected", "KILLED") and fake.calls == []
    await writes.restore_tool(
        env.factory, env.wa.tenant_id, "order_cancel", uuid.uuid4(), "all clear"
    )
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        assert await writes.killed(session, "order_cancel") is False
        assert await writes.killed(session, "refund_create") is False


async def test_global_pause_blocks_execution(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    action = await propose_and_confirm(env, conv, pol)
    async with session_scope(env.owner_factory, super_admin=True) as session:
        await session.execute(
            text("UPDATE platform_flags SET enabled = true WHERE name = 'global_pause'")
        )
    try:
        out = await run(env, fake, action, pol)
    finally:
        async with session_scope(env.owner_factory, super_admin=True) as session:
            await session.execute(
                text("UPDATE platform_flags SET enabled = false WHERE name = 'global_pause'")
            )
    assert out.detail == "KILLED" and fake.calls == []


async def test_anomaly_pauses_tool_after_threshold(
    env: AgentEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANOMALY_AUTO_THRESHOLD", "3")
    get_settings.cache_clear()
    try:
        conv = await make_conv(env)
        assert await writes.check_anomaly(env.factory, env.wa.tenant_id, "order_cancel") is False
        async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
            for n in range(3):
                await session.execute(
                    text(
                        "INSERT INTO agent_actions (conversation_id, action_type, idempotency_key,"
                        " status, tool_name, risk_tier, requires_approval)"
                        " VALUES (:c, 'tool_call', :k, 'confirmed', 'order_cancel', 'T2', false)"
                    ),
                    {"c": conv.conversation_id, "k": f"extra{n}"},
                )
        assert await writes.check_anomaly(env.factory, env.wa.tenant_id, "order_cancel") is True
        async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
            assert await writes.killed(session, "order_cancel") is True
            assert await writes.killed(session, "refund_create") is False
        assert await env.q("SELECT 1 FROM notifications WHERE title = 'Tool band kar diya gaya'")
    finally:
        get_settings.cache_clear()


async def test_address_update_flow(env: AgentEnv) -> None:
    conv, pol, fake = await make_conv(env), policy(), shop()
    action = await propose_and_confirm(
        env, conv, pol, tool="address_update", amount=0, args=dict(ADDRESS)
    )
    assert (await run(env, fake, action, pol)).status == "confirmed"
    assert fake.orders["#1001"].address_pin == "411001"
