from datetime import UTC, datetime, timedelta

from app.v1.gate import GateContext, GateOrder, evaluate
from app.v1.policy import PolicyV2
from app.voice.speech import digits_to_text, speakable, tts_language

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
TOOLS = ["handoff_to_human", "address_update", "order_cancel", "exchange_request", "refund_create"]


def auto_policy() -> PolicyV2:
    return PolicyV2.model_validate(
        {
            "enabled_tools": TOOLS,
            "autonomy": {t: "auto" for t in TOOLS[1:]},
            "limits": {},
        }
    )


def delivered_order() -> GateOrder:
    return GateOrder(
        order_ref="#1001",
        total_paise=40_000,
        fulfillment_status="fulfilled",
        financial_status="paid",
        cancelled=False,
        refunded_paise=0,
        delivered_at=NOW - timedelta(days=2),
    )


def test_speakable_reads_money_order_ids_and_drops_markup() -> None:
    text = "Order #1001 ka *Rs 400* refund 😀 ho gaya. Confirm ke liye 'haan' likhein."
    spoken = speakable(text, "hinglish")
    assert "1 0 0 1" in spoken and "400 rupaye" in spoken
    assert "*" not in spoken and "#" not in spoken and "😀" not in spoken
    assert "bolein" in spoken and "likhein" not in spoken


def test_speakable_reads_paise_and_english() -> None:
    assert "1250 rupees 50 paise" in speakable("Total Rs 1,250.50", "en")


def test_speakable_is_capped() -> None:
    assert len(speakable("ab " * 1000, "hinglish")) <= 400


def test_digits_and_language_mapping() -> None:
    assert digits_to_text("1001") == "mera order #1001 hai"
    assert tts_language("en") == "en-IN" and tts_language("hinglish") == "hi-IN"


def test_voice_refund_never_auto() -> None:
    policy = auto_policy()
    text = evaluate(policy, GateContext("refund_create", delivered_order(), NOW))
    voice = evaluate(policy, GateContext("refund_create", delivered_order(), NOW, channel="voice"))
    assert text.route == "auto"
    assert (voice.route, voice.reason, voice.amount_paise) == ("approval", "VOICE_REFUND", 40_000)


def test_voice_does_not_change_cancel_routing() -> None:
    policy = auto_policy()
    base = GateOrder("#1001", 40_000, "unfulfilled", "paid", False, 0, None)
    assert evaluate(policy, GateContext("order_cancel", base, NOW, channel="voice")).route == "auto"


def test_voice_exchange_never_auto() -> None:
    policy = auto_policy()
    order = delivered_order()
    text = evaluate(policy, GateContext("exchange_request", order, NOW))
    voice = evaluate(policy, GateContext("exchange_request", order, NOW, channel="voice"))
    assert text.route == "auto"
    assert (voice.route, voice.reason) == ("approval", "VOICE_EXCHANGE")


def test_voice_eval_suite_is_clean_and_large_enough() -> None:
    from app.evals import voice_safety

    report = voice_safety.run_all()
    assert report.failed == [] and report.total >= 50
