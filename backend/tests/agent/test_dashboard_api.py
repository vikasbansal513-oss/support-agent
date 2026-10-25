import httpx
from sqlalchemy import text

from app.core.db import session_scope
from tests.agent.helpers import AgentEnv, cls
from tests.auth.helpers import bearer, error_code
from tests.auth.test_admin_flow import _admin_login_with_mfa
from tests.channels.helpers import wa_message


async def send(env: AgentEnv, body: str, mid: str) -> None:
    await env.client.post(env.wa.path, json=wa_message(mid, body=body))
    await env.drain()


async def test_conversations_list_detail_masked_and_paged(env: AgentEnv) -> None:
    await env.publish_policy()
    env.provider.responses = [cls()]
    await send(env, "order #1001", "c1")
    listing = (await env.client.get("/conversations", headers=env.wa.headers)).json()
    assert len(listing["items"]) == 1
    customer = listing["items"][0]["customer"]
    assert customer["masked_phone"].endswith("3210") and "98765" not in customer["masked_phone"]
    assert customer["masked_email"] is None
    cid = listing["items"][0]["id"]
    detail = (await env.client.get(f"/conversations/{cid}", headers=env.wa.headers)).json()
    assert [m["direction"] for m in detail["messages"]] == ["in", "out"]
    assert detail["messages"][1]["action_status"] == "shadow"
    assert (await env.client.get("/conversations?status=resolved", headers=env.wa.headers)).json()[
        "items"
    ] == []
    missing = await env.client.get(
        "/conversations/00000000-0000-0000-0000-000000000000", headers=env.wa.headers
    )
    assert missing.status_code == 404


async def test_handoff_claim_resolve_flow_and_human_reply(env: AgentEnv) -> None:
    await env.publish_policy()
    await env.set_mode("auto")
    env.provider.responses = [cls(intent="other", ref=None, flags={"wants_human": True})]
    await send(env, "insaan chahiye", "h1")
    items = (await env.client.get("/handoffs", headers=env.wa.headers)).json()["items"]
    assert len(items) == 1 and items[0]["status"] == "open" and not items[0]["sla_breached"]
    hid, cid = items[0]["id"], items[0]["conversation_id"]
    claimed = await env.client.post(f"/handoffs/{hid}/claim", headers=env.wa.headers)
    assert claimed.status_code == 200 and claimed.json()["status"] == "claimed"
    conv = (await env.client.get(f"/conversations/{cid}", headers=env.wa.headers)).json()
    assert conv["human_active"] is True
    sent_before = len(env.sender.sent)
    reply = await env.client.post(
        f"/conversations/{cid}/reply",
        json={"content": "Namaste, main dekhta hoon"},
        headers=env.wa.headers,
    )
    assert reply.status_code == 200 and reply.json()["sender"] == "human"
    assert len(env.sender.sent) == sent_before + 1
    env.provider.responses = [cls()]
    await send(env, "order #1001 kahan hai", "h2")
    assert env.provider.responses != []
    assert len(env.sender.sent) == sent_before + 1
    done = await env.client.post(f"/handoffs/{hid}/resolve", headers=env.wa.headers)
    assert done.json()["status"] == "resolved"
    conv = (await env.client.get(f"/conversations/{cid}", headers=env.wa.headers)).json()
    assert conv["human_active"] is False and conv["status"] == "open"
    assert (await env.client.get("/handoffs", headers=env.wa.headers)).json()["items"] == []


async def test_handoff_claimed_by_other_user_conflicts(env: AgentEnv) -> None:
    await env.publish_policy()
    await env.set_mode("auto")
    env.provider.responses = [cls(intent="other", ref=None, flags={"wants_human": True})]
    await send(env, "insaan chahiye", "x1")
    hid = (await env.client.get("/handoffs", headers=env.wa.headers)).json()["items"][0]["id"]
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE handoffs SET status = 'claimed', assigned_user_id = gen_random_uuid()")
        )
    again = await env.client.post(f"/handoffs/{hid}/claim", headers=env.wa.headers)
    assert again.status_code == 409 and error_code(again) == "E-HO-001"


async def test_human_reply_blocked_when_window_closed_even_if_agent_paused(env: AgentEnv) -> None:
    await env.publish_policy()
    env.provider.responses = [cls()]
    await send(env, "order #1001", "w1")
    cid = (await env.client.get("/conversations", headers=env.wa.headers)).json()["items"][0]["id"]
    await env.set_paused(True)
    ok = await env.client.post(
        f"/conversations/{cid}/reply", json={"content": "hello"}, headers=env.wa.headers
    )
    assert ok.status_code == 200
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("UPDATE conversations SET last_inbound_at = now() - interval '30 hours'")
        )
    late = await env.client.post(
        f"/conversations/{cid}/reply", json={"content": "hello"}, headers=env.wa.headers
    )
    assert late.status_code == 409 and error_code(late) == "E-ACT-002"


async def test_shadow_review_verdicts_and_stats(env: AgentEnv) -> None:
    await env.publish_policy()
    env.provider.responses = [cls(), cls()]
    await send(env, "order #1001", "r1")
    await send(env, "status #1001 batao", "r2")
    queue = (
        await env.client.get("/actions?status=shadow&unreviewed=true", headers=env.wa.headers)
    ).json()
    assert len(queue["items"]) == 2
    first = queue["items"][0]
    assert first["customer_message"] and first["body_text"]
    stats = (await env.client.get("/dashboard/stats", headers=env.wa.headers)).json()
    assert stats["shadow_unreviewed"] == 2 and stats["mode"] == "shadow"
    assert stats["messages_today"] == 2 and stats["handled_by_agent_today"] >= 1
    marked = await env.client.post(
        f"/actions/{first['id']}/verdict", json={"verdict": "correct"}, headers=env.wa.headers
    )
    assert marked.json()["verdict"] == "correct"
    await env.client.post(
        f"/actions/{first['id']}/verdict",
        json={"verdict": "wrong", "note": "galat"},
        headers=env.wa.headers,
    )
    stats = (await env.client.get("/dashboard/stats", headers=env.wa.headers)).json()
    assert (stats["shadow_correct"], stats["shadow_wrong"], stats["shadow_unreviewed"]) == (0, 1, 1)
    left = (await env.client.get("/actions?unreviewed=true", headers=env.wa.headers)).json()
    assert len(left["items"]) == 1
    approve_shadow = await env.client.post(
        f"/actions/{first['id']}/approve", headers=env.wa.headers
    )
    assert approve_shadow.status_code == 409 and error_code(approve_shadow) == "E-ACT-001"
    assert env.sender.sent == []


async def test_draft_approve_and_reject_via_api(env: AgentEnv) -> None:
    await env.publish_policy()
    await env.set_mode("draft")
    env.provider.responses = [cls(), cls()]
    await send(env, "order #1001", "d1")
    await send(env, "order #1001 ka status", "d2")
    drafts = (await env.client.get("/actions?status=draft", headers=env.wa.headers)).json()["items"]
    assert len(drafts) == 2 and env.sender.sent == []
    ok = await env.client.post(f"/actions/{drafts[0]['id']}/approve", headers=env.wa.headers)
    assert ok.status_code == 200 and ok.json()["status"] == "sent" and len(env.sender.sent) == 1
    again = await env.client.post(f"/actions/{drafts[0]['id']}/approve", headers=env.wa.headers)
    assert error_code(again) == "E-ACT-001"
    no = await env.client.post(f"/actions/{drafts[1]['id']}/reject", headers=env.wa.headers)
    assert no.json()["status"] == "rejected" and len(env.sender.sent) == 1


async def test_report_wrong_answer_creates_handoff_and_silences_agent(env: AgentEnv) -> None:
    await env.publish_policy()
    await env.set_mode("auto")
    await env.set_notify_email("owner@brand.example")
    env.provider.responses = [cls()]
    await send(env, "order #1001", "q1")
    cid = (await env.client.get("/conversations", headers=env.wa.headers)).json()["items"][0]["id"]
    detail = (await env.client.get(f"/conversations/{cid}", headers=env.wa.headers)).json()
    out = next(m for m in detail["messages"] if m["direction"] == "out")
    resp = await env.client.post(
        f"/messages/{out['id']}/report", json={"reason": "total galat hai"}, headers=env.wa.headers
    )
    assert resp.status_code == 200 and resp.json()["reason_code"] == "CUSTOMER_REPORT"
    assert (await env.client.get(f"/conversations/{cid}", headers=env.wa.headers)).json()[
        "human_active"
    ]
    assert len(await env.q("SELECT 1 FROM answer_reports")) == 1
    customer_msg = next(m for m in detail["messages"] if m["direction"] == "in")
    bad = await env.client.post(
        f"/messages/{customer_msg['id']}/report", json={"reason": "galat"}, headers=env.wa.headers
    )
    assert bad.status_code == 404


async def test_usage_endpoint(env: AgentEnv) -> None:
    await env.publish_policy()
    env.provider.responses = [cls()]
    await send(env, "order #1001", "u1")
    usage = (await env.client.get("/usage", headers=env.wa.headers)).json()
    assert usage["conversation_count"] == 1 and usage["limit"] == 500
    assert usage["limit_reached"] is False


async def test_admin_mode_gate_needs_shadow_review(env: AgentEnv, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    monkeypatch.setenv("SHADOW_MIN_REVIEWED", "2")
    monkeypatch.setenv("SHADOW_MIN_CORRECT_PCT", "90")
    get_settings.cache_clear()
    await env.publish_policy()
    env.provider.responses = [cls(), cls()]
    await send(env, "order #1001", "g1")
    await send(env, "order #1001 status", "g2")
    _, verified, _ = await _admin_login_with_mfa(env.client, env.factory)
    admin = bearer(verified)
    url = f"/admin/tenants/{env.wa.tenant_id}/mode"
    blocked = await env.client.put(
        url, json={"mode": "draft", "reason": "pilot start"}, headers=admin
    )
    assert blocked.status_code == 409 and error_code(blocked) == "E-ADM-002"
    items = (await env.client.get("/actions?status=shadow", headers=env.wa.headers)).json()["items"]
    await env.client.post(
        f"/actions/{items[0]['id']}/verdict", json={"verdict": "correct"}, headers=env.wa.headers
    )
    await env.client.post(
        f"/actions/{items[1]['id']}/verdict", json={"verdict": "wrong"}, headers=env.wa.headers
    )
    assert (
        await env.client.put(url, json={"mode": "draft", "reason": "pilot start"}, headers=admin)
    ).status_code == 409
    await env.client.post(
        f"/actions/{items[1]['id']}/verdict", json={"verdict": "correct"}, headers=env.wa.headers
    )
    ok = await env.client.put(url, json={"mode": "draft", "reason": "pilot start"}, headers=admin)
    assert ok.status_code == 200 and ok.json()["mode"] == "draft"
    back = await env.client.put(
        url, json={"mode": "shadow", "reason": "rollback test"}, headers=admin
    )
    assert back.json()["mode"] == "shadow"
    brand = await env.client.put(
        url, json={"mode": "auto", "reason": "no way"}, headers=env.wa.headers
    )
    assert brand.status_code in (401, 403)
    get_settings.cache_clear()


async def test_agent_role_endpoints_need_login(client: httpx.AsyncClient) -> None:
    for path in ("/conversations", "/handoffs", "/actions", "/dashboard/stats", "/usage"):
        assert (await client.get(path)).status_code == 401
