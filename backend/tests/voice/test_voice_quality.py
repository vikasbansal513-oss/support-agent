import uuid

from sqlalchemy import text

from app.core.db import session_scope
from app.voice import service
from tests.agent.helpers import AgentEnv
from tests.v1.flow import ADDRESS, plan, setup
from tests.voice.helpers import CALLER, TRANSFER, Voice, _no_anomaly_stop, configure  # noqa: F401


async def seed_whatsapp(
    env: AgentEnv, order_ref: str | None = None, window_open: bool = True
) -> uuid.UUID:
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO customers (phone_e164) VALUES (:p)"
                " ON CONFLICT (tenant_id, phone_e164) WHERE phone_e164 IS NOT NULL DO NOTHING"
            ),
            {"p": CALLER},
        )
        customer = (
            await session.execute(
                text("SELECT id FROM customers WHERE phone_e164 = :p"), {"p": CALLER}
            )
        ).scalar_one()
        conv = (
            await session.execute(
                text(
                    "INSERT INTO conversations (customer_id, channel, active_order_ref,"
                    " last_inbound_at, last_message_at) VALUES (:c, 'whatsapp', :o,"
                    " CASE WHEN :w THEN now() ELSE now() - interval '3 days' END, now())"
                    " RETURNING id"
                ),
                {"c": customer, "o": order_ref, "w": window_open},
            )
        ).scalar_one()
    return uuid.UUID(str(conv))


async def test_other_provider_payload_shape_is_mapped_both_ways(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env, provider="fake_ivr")
    base = {"CallSid": "X-1", "From": CALLER, "To": voice.called}
    start = await env.client.post(voice.path, json={**base, "Type": "CallStart"})
    assert start.status_code == 200
    body = start.json()
    assert body["Next"] == "Listen" and body["BargeIn"] is False and "AI sahayak" in body["Say"]
    env.provider.responses = [plan("order_status", "answer", None)]
    turn = await env.client.post(
        voice.path,
        json={
            **base,
            "Type": "SpeechInput",
            "SpeechResult": "mera order #1001 kahan hai",
            "Confidence": "0.91",
        },
    )
    assert "1 0 0 1" in turn.json()["Say"]
    junk = await env.client.post(voice.path, json={**base, "Type": "Nope"})
    assert junk.json()["Next"] == "Hangup"


async def test_unknown_provider_is_rejected(env: AgentEnv) -> None:
    await setup(env, "auto")
    resp = await env.client.put(
        "/voice/settings",
        json={"called_number": "+911155550000", "provider": "made_up"},
        headers=env.wa.headers,
    )
    assert resp.status_code == 422


async def test_greeting_discloses_ai_and_transcript_and_cannot_be_interrupted(
    env: AgentEnv,
) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    out = await voice.start()
    assert "AI sahayak" in out["say"] and "text" in out["say"] and out["barge_in"] is False
    later = await voice.say("mera order #1001 kahan hai", plan("order_status", "answer", None))
    assert later["barge_in"] is True


async def test_latency_is_counted_per_call_and_slow_turns_flagged(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    await voice.say("mera order #1001 kahan hai", plan("order_status", "answer", None))
    row = (await env.q("SELECT latency_total_ms, latency_max_ms, slow_turns FROM voice_calls"))[0]
    assert row.latency_total_ms > 0 and row.latency_max_ms > 0
    tenant = service.VoiceTenant(env.wa.tenant_id, voice.called, True, None)
    await service._record_latency(env.factory, tenant, voice.call_id, 9000)
    row = (await env.q("SELECT latency_max_ms, slow_turns FROM voice_calls"))[0]
    assert row.latency_max_ms == 9000 and row.slow_turns == 1


async def test_caller_number_remembers_the_last_order_from_another_channel(env: AgentEnv) -> None:
    await setup(env, "auto")
    await seed_whatsapp(env, "#1001")
    voice = await configure(env)
    await voice.start()
    assert (await env.q("SELECT order_ref FROM voice_calls"))[0].order_ref == "#1001"
    out = await voice.say(
        "mera pichla order kahan hai", plan("order_status", "answer", None, ref=None)
    )
    assert "1 0 0 1" in out["say"] and "400 rupaye" in out["say"]


async def test_order_memory_expires_and_never_invents_an_order(env: AgentEnv, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VOICE_ORDER_MEMORY_DAYS", "0")
    from app.core.config import get_settings

    get_settings.cache_clear()
    await setup(env, "auto")
    await seed_whatsapp(env, "#1001")
    voice = await configure(env)
    await voice.start()
    assert (await env.q("SELECT order_ref FROM voice_calls"))[0].order_ref is None


async def test_handoff_summary_carries_order_what_was_said_and_what_was_done(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env, transfer=TRANSFER)
    await voice.start()
    await voice.say("mera order #1001 kahan hai", plan("order_status", "answer", None))
    out = await voice.say(
        "address badalna hai",
        plan("address_change", "clarify", None, args={"new_address": ADDRESS}),
    )
    assert out["action"] == "transfer"
    assert "1001" in out["briefing"] and "address badalna" in out["briefing"]
    summary = (await env.q("SELECT summary FROM handoffs"))[0].summary
    assert summary["order_ref"] == "#1001"
    assert "address badalna" in summary["customer_asked"]
    assert any("baatein" in item for item in summary["agent_did"])
    assert summary["order_ref"] == "#1001"


async def test_failed_transfer_becomes_an_urgent_callback_with_an_alert(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env, transfer=TRANSFER)
    await voice.start()
    out = await voice.say("kisi insaan se baat karni hai")
    assert out["action"] == "transfer"
    await voice.send("end", reason="no_answer")
    row = (await env.q("SELECT status, end_reason FROM voice_calls"))[0]
    assert (row.status, row.end_reason) == ("callback", "transfer_failed")
    assert (await env.q("SELECT priority FROM handoffs"))[0].priority == "urgent"
    assert await env.q("SELECT 1 FROM notifications WHERE title = 'Transfer nahi lag paya'")


async def test_answered_transfer_is_not_flagged_as_failed(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env, transfer=TRANSFER)
    await voice.start()
    await voice.say("kisi insaan se baat karni hai")
    await voice.send("end", reason="caller_hangup")
    assert (await env.q("SELECT status FROM voice_calls"))[0].status == "transferred"
    assert not await env.q("SELECT 1 FROM notifications WHERE title = 'Transfer nahi lag paya'")


async def test_call_summary_goes_to_whatsapp_when_the_window_is_open(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    await seed_whatsapp(env)
    voice = await configure(env)
    await voice.start()
    await voice.say("order 1001 cancel kar do", plan())
    await voice.say("haan", confidence=0.95)
    await voice.send("end", reason="caller_hangup")
    assert (await env.q("SELECT summary_status FROM voice_calls"))[0].summary_status == "sent"
    texts = [m[1] if isinstance(m, tuple) else str(m) for m in env.sender.sent]
    assert any("saaransh" in t and "#1001" in t for t in texts)
    await voice.send("end", reason="caller_hangup")
    assert len([t for t in map(str, env.sender.sent) if "saaransh" in t]) == 1


async def test_call_summary_is_skipped_when_whatsapp_window_is_closed(env: AgentEnv) -> None:
    await setup(env, "auto")
    await seed_whatsapp(env, window_open=False)
    voice = await configure(env)
    await voice.start()
    await voice.say("mera order #1001 kahan hai", plan("order_status", "answer", None))
    await voice.send("end", reason="caller_hangup")
    assert (await env.q("SELECT summary_status FROM voice_calls"))[0].summary_status == "skipped"
    assert not [t for t in map(str, env.sender.sent) if "saaransh" in t]


async def test_stats_and_feedback(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    await voice.say("mera order #1001 kahan hai", plan("order_status", "answer", None))
    await voice.send("end", reason="caller_hangup")
    voice.call_id = "CA-2"
    await voice.start()
    await voice.say("khrr", confidence=0.1)
    await voice.say("khrr", confidence=0.1)
    calls = (await env.client.get("/voice/calls", headers=env.wa.headers)).json()
    call_id = next(c["id"] for c in calls if c["status"] == "completed")
    fb = await env.client.post(
        f"/voice/calls/{call_id}/feedback",
        json={"verdict": "good", "note": "sahi jawab"},
        headers=env.wa.headers,
    )
    assert fb.json()["feedback"] == "good"
    bad_id = next(c["id"] for c in calls if c["status"] == "callback")
    await env.client.post(
        f"/voice/calls/{bad_id}/feedback", json={"verdict": "bad"}, headers=env.wa.headers
    )
    stats = (await env.client.get("/voice/stats?days=7", headers=env.wa.headers)).json()
    assert stats["calls"] == 2 and stats["handled_by_agent"] == 1 and stats["callback"] == 1
    assert (stats["feedback_good"], stats["feedback_bad"]) == (1, 1)
    assert stats["agent_rate_percent"] == 50 and stats["avg_latency_ms"] >= 0
    missing = await env.client.post(
        f"/voice/calls/{uuid.uuid4()}/feedback", json={"verdict": "good"}, headers=env.wa.headers
    )
    assert missing.status_code == 404
