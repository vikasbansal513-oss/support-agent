import dataclasses

from app.voice import speech
from app.voice.service import WHY, CallRow


def test_greeting_asks_an_explicit_yes_no_question_in_every_language() -> None:
    # Decision Framework 17.1: disclosure must end in an explicit consent question, not
    # straight into "how can I help".
    for lang in ("hinglish", "en", "hi"):
        line = speech.voice_line("greeting", lang, brand="Acme")
        assert "Acme" in line
        assert "?" in line


def test_consent_ack_and_reask_templates_exist_for_every_language() -> None:
    for lang in ("hinglish", "en", "hi"):
        assert speech.voice_line("consent_ack", lang)
        assert speech.voice_line("consent_reask", lang)


def test_call_row_tracks_consent_response() -> None:
    fields = {f.name for f in dataclasses.fields(CallRow)}
    assert "consent_response" in fields


def test_why_dict_covers_every_new_consent_reason() -> None:
    for reason in ("HUMAN_REQUESTED", "CONSENT_DECLINED"):
        assert reason in WHY and WHY[reason]


def test_why_dict_covers_silence_no_response() -> None:
    # Section 17.4: silence-limit now ends via the handoff/callback path, not a bare goodbye.
    assert "SILENCE_NO_RESPONSE" in WHY and WHY["SILENCE_NO_RESPONSE"]


def test_has_spoken_number_detects_order_ref_and_amount_readouts() -> None:
    # Section 17.2: an order ref or amount being read out should not be a false barge-in
    # trigger point - detected after speakable() has turned both into digit sequences.
    assert speech.has_spoken_number(speech.speakable("Order #1001 confirm karein", "hinglish"))
    assert speech.has_spoken_number(speech.speakable("Total Rs 1,250.50", "en"))


def test_has_spoken_number_false_for_plain_text() -> None:
    assert not speech.has_spoken_number(speech.speakable("Kya aap theek hain?", "hinglish"))
    assert not speech.has_spoken_number("Thank you for calling.")
