import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.core import timeutil
from app.core.db import session_scope
from app.jobs import ops
from app.policy import service as policy_service
from app.v1 import reply as reply_layer
from app.v1.policy import PolicyV2
from tests.agent.helpers import AgentEnv
from tests.ops.test_privacy import ctx
from tests.v1.flow import plan, say, setup

V2_TOOLS = [
    "handoff_to_human",
    "order_lookup",
    "tracking_lookup",
    "order_cancel",
    "return_request",
]


def _policy(**extra: object) -> PolicyV2:
    return PolicyV2.model_validate({"schema_version": 2, **extra})


async def _extras(env: AgentEnv, **extra: object) -> None:
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE policies SET content = content || CAST(:e AS jsonb)"
                " WHERE status = 'published'"
            ),
            {"e": json.dumps(extra)},
        )
    policy_service.invalidate_published_policy_cache()


def test_validators() -> None:
    p = _policy(
        faqs=[{"question": "  Do you  ship  to Goa? ", "answer": "Yes, 4 to 6 days."}],
        ooo_message="  We are   closed  ",
        cod_refund_method="bank_transfer",
        handoff_reminder_minutes=10,
    )
    assert p.faqs[0].question == "Do you ship to Goa?"
    assert p.ooo_message == "We are closed"
    for bad in (
        {"faqs": [{"question": "a", "answer": "long enough"}]},
        {"faqs": [{"question": "ok question", "answer": "x" * 401}]},
        {"faqs": [{"question": "q" * 5, "answer": "a" * 5}] * 21},
        {"cod_refund_method": "cash"},
        {"handoff_reminder_minutes": 3},
        {"ooo_message": "x" * 301},
        {"cod_refund_note": "x" * 201},
    ):
        with pytest.raises(ValidationError):
            _policy(**bad)
    assert _policy().handoff_reminder_minutes == 0


def test_policy_facts_unchanged_by_default_and_extended_when_set() -> None:
    base = set(reply_layer.policy_facts(_policy()))
    assert base == {
        "return_window_days",
        "final_sale_tags",
        "support_hours",
        "team_reply_within_minutes",
    }
    facts = reply_layer.policy_facts(
        _policy(
            faqs=[{"question": "COD available?", "answer": "Yes up to Rs 5000."}],
            cod_refund_method="store_credit",
            cod_refund_note="Within 7 days",
        )
    )
    assert facts["store_faq"] == [{"question": "COD available?", "answer": "Yes up to Rs 5000."}]
    assert facts["cod_refund_method"] == "store credit"
    assert facts["cod_refund_note"] == "Within 7 days"


async def test_try_endpoint_gate_and_office_checks(env: AgentEnv) -> None:
    body = {
        "schema_version": 2,
        "office_hours": {"start": "10:00", "end": "18:00", "days": [0, 1, 2, 3, 4]},
        "enabled_tools": V2_TOOLS,
        "autonomy": {"order_cancel": "approval", "return_request": "approval"},
        "return_window_by_tag": {"shoes": 15},
    }
    created = await env.client.post("/policies", json={"content": body}, headers=env.wa.headers)
    assert created.status_code == 201, created.text
    url = f"/policies/{created.json()['id']}/try"
    sat = "2026-10-24T06:00:00Z"

    async def check(**over: object) -> dict[str, object]:
        payload = {
            "tool": "order_cancel",
            "order_total_rupees": 500,
            "fulfillment_status": "unfulfilled",
            "at": sat,
            **over,
        }
        resp = await env.client.post(url, json=payload, headers=env.wa.headers)
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    first = await check()
    assert (first["route"], first["reason"]) == ("approval", "LEVEL_APPROVAL")
    assert first["office_open"] is False and first["next_open"] == "2026-10-26T04:30:00Z"
    shipped = await check(fulfillment_status="fulfilled", delivered_days_ago=1)
    assert (shipped["route"], shipped["reason"]) == ("handoff", "ALREADY_SHIPPED")
    late = await check(tool="return_request", fulfillment_status="fulfilled", delivered_days_ago=10)
    assert (late["route"], late["reason"]) == ("decline", "WINDOW_EXPIRED")
    assert late["return_window_days"] == 7
    shoes = await check(
        tool="return_request",
        fulfillment_status="fulfilled",
        delivered_days_ago=10,
        tags=["Shoes"],
    )
    assert (shoes["route"], shoes["return_window_days"]) == ("approval", 15)
    weekday = await check(at="2026-10-26T05:30:00Z")
    assert weekday["office_open"] is True and weekday["next_open"] is None
    off = await env.client.post(
        url,
        json={
            "tool": "refund_create",
            "order_total_rupees": 1,
            "fulfillment_status": "unfulfilled",
        },
        headers=env.wa.headers,
    )
    assert off.json()["route"] == "handoff"
    bad = await env.client.post(url, json={"tool": "nope"}, headers=env.wa.headers)
    assert bad.status_code == 422


async def test_try_endpoint_needs_v2_policy(env: AgentEnv) -> None:
    created = await env.client.post(
        "/policies", json={"content": {"schema_version": 1}}, headers=env.wa.headers
    )
    assert created.status_code == 201, created.text
    resp = await env.client.post(
        f"/policies/{created.json()['id']}/try",
        json={"tool": "order_cancel", "order_total_rupees": 1, "fulfillment_status": "unfulfilled"},
        headers=env.wa.headers,
    )
    assert resp.status_code == 422


async def test_change_reason_is_stored_in_audit_log(env: AgentEnv) -> None:
    created = await env.client.post(
        "/policies",
        json={"content": {"schema_version": 2}, "reason": "Restored version 2"},
        headers=env.wa.headers,
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    updated = await env.client.put(
        f"/policies/{pid}",
        json={"content": {"schema_version": 2, "return_window_days": 9}, "reason": "Festival"},
        headers=env.wa.headers,
    )
    assert updated.status_code == 200, updated.text
    rows = await env.q(
        "SELECT action, reason FROM audit_log WHERE action IN"
        " ('policy.draft_created','policy.draft_updated') ORDER BY occurred_at"
    )
    assert [(r.action, r.reason) for r in rows] == [
        ("policy.draft_created", "Restored version 2"),
        ("policy.draft_updated", "Festival"),
    ]
    too_long = await env.client.post(
        "/policies",
        json={"content": {"schema_version": 2}, "reason": "x" * 301},
        headers=env.wa.headers,
    )
    assert too_long.status_code in (400, 422)


async def test_faq_answer_reaches_the_reply_writer_as_facts(env: AgentEnv) -> None:
    await setup(env)
    await _extras(
        env,
        faqs=[{"question": "Do you ship to Goa?", "answer": "Yes, delivery takes 4 to 6 days."}],
    )
    reply_json = json.dumps(
        {
            "text": "Yes, we ship to Goa. Delivery takes 4 to 6 days.",
            "language": "en",
            "facts_used": ["store_faq"],
            "promises": [],
            "needs_human": False,
        }
    )
    env.provider.responses = [
        plan("policy_question", "answer", None, ref=None, language="en"),
        reply_json,
    ]
    await say(env, "Do you ship to Goa?", "f1")
    assert "4 to 6 days" in env.sender.sent[-1][1]
    user_prompt = env.provider.calls[-1][1]
    assert "store_faq" in user_prompt and "Do you ship to Goa?" in user_prompt
    assert await env.q("SELECT reason_code FROM handoffs") == []


async def test_out_of_office_text_is_added_to_after_hours_handoff_only(env: AgentEnv) -> None:
    await setup(env)
    today = timeutil.to_ist(datetime.now(UTC)).date().isoformat()
    await _extras(env, holidays=[today], ooo_message="We are closed for Diwali.")
    await say(env, "mujhe kisi insaan se baat karni hai", "o1", plan("other", "handoff", None))
    assert "We are closed for Diwali." in env.sender.sent[-1][1]
    await _extras(env, holidays=[])
    env.sender.sent.clear()
    await env.q("SELECT 1")
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(text("UPDATE handoffs SET status = 'resolved', resolved_at = now()"))
        await session.execute(text("UPDATE conversations SET status = 'open'"))
    await say(env, "mujhe kisi insaan se baat karni hai", "o2", plan("other", "handoff", None))
    assert env.sender.sent and "Diwali" not in env.sender.sent[-1][1]


async def _overdue_handoff(env: AgentEnv) -> None:
    await say(env, "mujhe kisi insaan se baat karni hai", "h1", plan("other", "handoff", None))
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "UPDATE handoffs SET sla_due_at = now() - interval '1 hour', would_handoff = false"
            )
        )


async def test_handoff_reminder_emails_only_when_the_brand_set_minutes(
    env: AgentEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "get_mailer", lambda: env.mailer)
    await setup(env)
    await env.set_notify_email("owner@brand.example")
    await _overdue_handoff(env)
    env.mailer.sent.clear()
    first = await ops.sla_check(ctx(env, "sla_check"))
    assert first["alerted"] >= 1
    assert not any(subject == "[Support] Handoff reminder" for _, subject, _ in env.mailer.sent)
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE handoffs SET last_alert_at = now() - interval '2 hours'")
        )
    await _extras(env, handoff_reminder_minutes=10)
    again = await ops.sla_check(ctx(env, "sla_check"))
    assert again["alerted"] >= 1
    assert any(
        to == "owner@brand.example" and subject == "[Support] Handoff reminder"
        for to, subject, _ in env.mailer.sent
    )
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE handoffs SET last_alert_at = now() - interval '5 minutes'")
        )
    assert (await ops.sla_check(ctx(env, "sla_check")))["alerted"] == 0
