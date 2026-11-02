from typing import Any

from tests.agent.helpers import AgentEnv
from tests.v1.flow import ADDRESS, plan, setup
from tests.v1.test_multiturn_eval import delivered_facts
from tests.voice.helpers import CALLED, CALLER, TRANSFER, Voice, _no_anomaly_stop, configure  # noqa: F401


async def reasons(env: AgentEnv) -> list[str]:
    return [h.reason_code for h in await env.q("SELECT reason_code FROM handoffs")]


async def call_row(env: AgentEnv) -> Any:
    return (await env.q("SELECT status, turn_count, end_reason FROM voice_calls"))[0]


async def test_start_greets_as_an_ai_and_opens_a_voice_conversation(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    out = await voice.start()
    assert out["action"] == "continue" and "AI sahayak" in out["say"] and out["language"] == "hi-IN"
    conv = await env.q("SELECT channel, status FROM conversations")
    assert [(c.channel, c.status) for c in conv] == [("voice", "open")]
    assert await env.q("SELECT 1 FROM consents WHERE purpose = 'voice_contact'")
    again = await voice.start()
    assert again["action"] == "continue"
    assert len(await env.q("SELECT 1 FROM voice_calls")) == 1


async def test_status_answer_is_spoken_with_digits_and_rupees(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    out = await voice.say("mera order #1001 kahan hai", plan("order_status", "answer", None))
    assert out["action"] == "continue"
    assert "1 0 0 1" in out["say"] and "400 rupaye" in out["say"]
    assert "#" not in out["say"] and "Rs" not in out["say"]
    assert await env.q("SELECT 1 FROM agent_actions WHERE action_type = 'reply_customer'")


async def test_cancel_by_voice_needs_a_clear_yes_and_then_executes_once(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    asked = await voice.say("order 1001 cancel kar do", plan())
    assert "cancel order" in asked["say"] and "400 rupaye" in asked["say"]
    assert shop.calls == []
    blurry = await voice.say("haan", confidence=0.5)
    assert "haan ya nahi" in blurry["say"] and shop.calls == []
    done = await voice.say("haan", confidence=0.93)
    assert "has been cancelled" in done["say"] and done["action"] == "continue"
    assert shop.orders["#1001"].cancelled is True and len(shop.calls) == 1


async def test_no_cancels_nothing(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    await voice.say("order 1001 cancel kar do", plan())
    out = await voice.say("nahi", confidence=0.9)
    assert "no changes have been made" in out["say"] and shop.calls == []


async def test_refund_on_a_call_always_waits_for_a_human_even_at_auto(env: AgentEnv) -> None:
    tools, shop = await setup(env, "auto")
    tools.orders["#2001"] = delivered_facts("#2001", 40_000)
    shop.add_order("#2001", total_paise=40_000, fulfillment_status="fulfilled")
    voice = await configure(env)
    await voice.start()
    await voice.say(
        "order 2001 ka refund chahiye",
        plan("refund", "propose", "refund_create", "#2001", args={"reason": "damaged"}),
    )
    out = await voice.say("haan", confidence=0.95)
    assert "call karke batayegi" in out["say"] and shop.calls == []
    (action,) = await env.q(
        "SELECT id, status FROM agent_actions WHERE tool_name = 'refund_create'"
    )
    assert action.status == "pending_approval"
    approved = await env.client.post(f"/approvals/{action.id}/approve", headers=env.wa.headers)
    assert (
        approved.json()["status"] == "confirmed" and shop.orders["#2001"].refunded_paise == 40_000
    )
    after = await env.q(
        "SELECT status, error FROM agent_actions WHERE action_type = 'reply_customer'"
    )
    assert "VOICE_CALL_ENDED" in {a.error for a in after}
    note = await env.q(
        "SELECT title FROM notifications WHERE title = 'Customer ko call karke batayein'"
    )
    assert note


async def test_address_change_is_not_taken_by_voice(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    out = await voice.say(
        "address badalna hai",
        plan("address_change", "clarify", None, args={"new_address": ADDRESS}),
    )
    assert out["action"] == "hangup" and "call karke" in out["say"]
    assert await reasons(env) == ["POLICY_EXCEPTION"] and shop.calls == []
    assert (await call_row(env)).status == "callback"


async def test_handoff_transfers_the_live_call_inside_office_hours(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env, transfer=TRANSFER)
    await voice.start()
    out = await voice.say("mujhe kisi insaan se baat karni hai")
    assert out["action"] == "transfer" and out["transfer_to"] == TRANSFER
    assert (await call_row(env)).status == "transferred"
    after = await voice.say("hello")
    assert after["action"] == "hangup"


async def test_two_unclear_audio_turns_hand_off(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    first = await voice.say("khrrr", confidence=0.2)
    assert "dobara bol" in first["say"] and first["action"] == "continue"
    second = await voice.say("khrr", confidence=0.1)
    assert second["action"] == "hangup" and await reasons(env) == ["CONFUSED"]


async def test_silence_twice_ends_the_call(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    first = await voice.send("turn", speech="", confidence=None)
    assert "line par" in first["say"] and first["action"] == "continue"
    second = await voice.send("turn", speech="")
    assert second["action"] == "hangup" and (await call_row(env)).end_reason == "silence"


async def test_keypad_digits_become_the_order_number(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    voice.env.provider.responses = [plan("order_status", "answer", None)]
    out = await voice.send("turn", speech="", digits="1001")
    assert "1 0 0 1" in out["say"]
    stored = await env.q(
        "SELECT content FROM messages WHERE direction = 'in' AND channel = 'voice'"
    )
    assert stored[0].content == "mera order #1001 hai"


async def test_provider_retry_of_the_same_turn_is_answered_once(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    first = await voice.say(
        "mera order #1001 kahan hai", plan("order_status", "answer", None), turn_id="t-1"
    )
    second = await voice.say(
        "mera order #1001 kahan hai", plan("order_status", "answer", None), turn_id="t-1"
    )
    assert first == second
    assert (
        len(await env.q("SELECT 1 FROM messages WHERE direction = 'in' AND channel = 'voice'")) == 1
    )


async def test_agent_not_in_auto_mode_never_talks_for_the_brand(env: AgentEnv) -> None:
    await setup(env, "auto", mode="shadow")
    voice = await configure(env)
    out = await voice.start()
    assert out["action"] == "hangup" and "call" in out["say"]
    assert (await call_row(env)).status == "callback"


async def test_disabled_voice_and_paused_agent_hand_to_humans(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env, enabled=False)
    assert (await voice.start())["action"] == "hangup"
    voice.call_id = "CA-2"
    voice = await configure(env, enabled=True)
    voice.call_id = "CA-2"
    await env.set_paused(True)
    assert (await voice.start())["action"] == "hangup"


async def test_end_event_completes_and_blocks_further_turns(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    await voice.send("end", reason="caller_hangup")
    row = await call_row(env)
    assert (row.status, row.end_reason) == ("completed", "caller_hangup")
    assert (await voice.say("hello"))["action"] == "hangup"


async def test_call_over_the_turn_limit_goes_to_a_human(env: AgentEnv, monkeypatch: Any) -> None:
    monkeypatch.setenv("VOICE_MAX_TURNS", "3")
    from app.core.config import get_settings

    get_settings.cache_clear()
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    for _ in range(3):
        await voice.say("mera order #1001 kahan hai", plan("order_status", "answer", None))
    out = await voice.say("phir se batao", plan("order_status", "answer", None))
    assert out["action"] == "hangup" and "STEP_LIMIT" in await reasons(env)


async def test_unknown_token_and_wrong_number(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    missing = await env.client.post("/webhooks/voice/not-a-real-token", json={"event": "start"})
    assert missing.status_code == 404
    wrong = await env.client.post(
        voice.path,
        json={"event": "start", "call_id": "x", "from": CALLER, "to": "+911199999999"},
    )
    assert wrong.status_code == 200 and wrong.json()["action"] == "hangup"
    junk = await env.client.post(voice.path, content=b"not json")
    assert junk.status_code == 200 and junk.json()["action"] == "hangup"
    assert await env.q("SELECT 1 FROM voice_calls") == []
    assert CALLED


async def test_agent_failure_mid_call_is_a_polite_handoff(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure(env)
    await voice.start()
    env.provider.responses = []
    out = await voice.say("order 1001 cancel kar do")
    assert out["action"] == "hangup"
    assert await reasons(env)
