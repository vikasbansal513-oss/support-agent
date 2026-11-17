from app.speech.providers import FakeSpeechToText, FakeTextToSpeech, SttResult
from app.voice.media_stream import EdesyStreamSession, OutMessage, UtteranceDetector, _chunk_audio
from app.voice.service import VoiceTenant
from tests.agent.helpers import AgentEnv
from tests.v1.flow import plan, setup
from tests.voice.helpers import CALLER, configure_edesy

SAMPLE_RATE = 16000


def _tone(n_bytes: int, amplitude: int) -> bytes:
    sample = amplitude.to_bytes(2, "little", signed=True)
    return sample * (n_bytes // 2)


def loud_audio(ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    return _tone(int(sample_rate * ms / 1000) * 2, 10_000)


def quiet_audio(ms: int, sample_rate: int = SAMPLE_RATE) -> bytes:
    return _tone(int(sample_rate * ms / 1000) * 2, 0)


def metadata_frame(from_number: str = CALLER, to_number: str | None = None) -> dict[str, str]:
    frame = {"callSid": "CA-edesy-1", "from": from_number, "direction": "inbound"}
    if to_number:
        frame["to"] = to_number
    return frame


def test_utterance_detector_stays_silent_on_quiet_audio() -> None:
    detector = UtteranceDetector(sample_rate_hz=SAMPLE_RATE)
    assert detector.push(quiet_audio(2000)) is None


def test_utterance_detector_emits_after_speech_then_silence() -> None:
    detector = UtteranceDetector(sample_rate_hz=SAMPLE_RATE)
    assert detector.push(loud_audio(500)) is None
    # 2026-11-17 VAD retune: end_silence_frames raised 25 -> 40 (~500ms -> ~800ms,
    # see UtteranceDetector's own docstring-comment) so a real call's natural
    # mid-sentence pause doesn't end the utterance early - a real call's logged VAD
    # stats showed utterances ending after only 2-11 loud frames out of 134-222,
    # cut short well before useful speech reached STT. This test's silence duration
    # moves with it (900ms > the new 800ms threshold) so it still exercises the same
    # "speech then enough silence ends the utterance" behaviour, not a shorter one.
    utterance = detector.push(quiet_audio(900))
    assert utterance is not None
    assert len(utterance) > 0
    # back to idle: more silence should not re-emit anything
    assert detector.push(quiet_audio(200)) is None


def test_utterance_detector_force_flushes_at_max_length() -> None:
    detector = UtteranceDetector(sample_rate_hz=SAMPLE_RATE, max_utterance_ms=1000)
    utterance = detector.push(loud_audio(3000))
    assert utterance is not None
    assert len(utterance) == detector._max_bytes


def test_chunk_audio_splits_into_20ms_frames() -> None:
    pcm = b"\x01\x02" * 1000
    frames = _chunk_audio(pcm, SAMPLE_RATE)
    frame_bytes = int(SAMPLE_RATE * 20 / 1000) * 2
    assert all(len(f) <= frame_bytes for f in frames)
    assert b"".join(frames) == pcm


def test_chunk_audio_empty_is_empty() -> None:
    assert _chunk_audio(b"", SAMPLE_RATE) == []


def _session(
    env: AgentEnv, voice: VoiceTenant
) -> tuple[EdesyStreamSession, FakeSpeechToText, FakeTextToSpeech]:
    stt = FakeSpeechToText()
    tts = FakeTextToSpeech()
    session = EdesyStreamSession(env.factory, voice, stt, tts, SAMPLE_RATE)
    return session, stt, tts


async def test_metadata_frame_starts_the_call_and_speaks_a_greeting(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure_edesy(env)
    session, _stt, tts = _session(env, voice)
    out = await session.handle_metadata(metadata_frame(to_number=voice.called_number))
    assert any(o.kind == "audio" for o in out)
    assert tts.calls and "AI sahayak" in tts.calls[0]
    row = (await env.q("SELECT status FROM voice_calls"))[0]
    assert row.status == "in_progress"


async def test_audio_turn_runs_stt_then_agent_then_tts(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure_edesy(env)
    session, stt, tts = _session(env, voice)
    await session.handle_metadata(metadata_frame(to_number=voice.called_number))
    stt.scripted.append(SttResult("mera order #1001 kahan hai", 0.95))
    env.provider.responses = [plan("order_status", "answer", None)]
    out: list[OutMessage] = []
    out += await session.handle_audio_chunk(loud_audio(500))
    # 900ms, not 600ms - see test_utterance_detector_emits_after_speech_then_silence's
    # comment: end_silence_frames moved 25 -> 40 (~800ms) in the 2026-11-17 VAD retune.
    out += await session.handle_audio_chunk(quiet_audio(900))
    assert stt.calls, "utterance should have reached the fake STT"
    assert any(o.kind == "audio" for o in out)
    assert tts.calls[-1] != tts.calls[0]  # a real, different reply was spoken
    assert "#" not in tts.calls[-1] and "Rs" not in tts.calls[-1]  # speakable() already ran
    assert await env.q("SELECT 1 FROM agent_actions WHERE action_type = 'reply_customer'")


async def test_audio_is_muted_for_our_own_reply_duration_even_if_delivered_late(
    env: AgentEnv,
) -> None:
    # ROOT-CAUSE FIX (2026-11-17), v2: real-call evidence (repeated empty STT results,
    # utterances always ending at ~the theoretical minimum length regardless of the
    # VAD energy_threshold used) pointed to handle_audio_chunk's old unconditional
    # self._detector.push(chunk) - every inbound chunk was fed to the VAD even while
    # we were still sending our own reply/greeting audio, so echo/leakage of our own
    # voice on the line could be misread as the caller speaking and close the
    # utterance before the caller had a real chance to reply. A plain before/after
    # bool isn't enough to test (or fix) this: app.webhooks.voice_stream's receive
    # loop is strictly sequential, so audio Edesy sent *during* our reply is only
    # ever delivered to handle_audio_chunk *after* our reply has fully finished
    # sending (confirmed by that module's own MAX_FRAME_BYTES comment, where a real
    # call's first post-greeting frame arrived as one ~3-second buffered chunk, not
    # steady 20ms frames) - a bool would already be back to False by then. This pins
    # down the byte-budget fix instead: audio delivered while _mute_bytes_remaining is
    # still owed (set from this reply's own total_pcm_bytes, regardless of when that
    # audio is actually handed to us) must be discarded, and audio delivered once the
    # budget is fully drained must still reach STT exactly as before.
    await setup(env, "auto")
    voice = await configure_edesy(env)
    session, stt, _tts = _session(env, voice)
    await session.handle_metadata(metadata_frame(to_number=voice.called_number))
    stt.scripted.append(SttResult("mera order #1001 kahan hai", 0.95))
    env.provider.responses = [plan("order_status", "answer", None)]
    # Simulate Edesy handing us a large late-arriving backlog chunk covering (and
    # exceeding) the two chunks below, as if it had buffered them during our reply.
    session._mute_bytes_remaining = len(loud_audio(500)) + len(quiet_audio(900)) + 1
    await session.handle_audio_chunk(loud_audio(500))
    await session.handle_audio_chunk(quiet_audio(900))
    assert not stt.calls, "audio owed to our own reply's playback must not reach STT"
    assert session._mute_bytes_remaining == 1, "budget should drain exactly by bytes seen"
    session._mute_bytes_remaining = 0
    await session.handle_audio_chunk(loud_audio(500))
    await session.handle_audio_chunk(quiet_audio(900))
    assert stt.calls, "audio received once the mute budget is drained should reach STT"


async def test_hangup_action_sends_disconnect_control(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure_edesy(env, enabled=False)
    session, _stt, _tts = _session(env, voice)
    out = await session.handle_metadata(metadata_frame(to_number=voice.called_number))
    assert any(o.kind == "control" and o.data.get("type") == "disconnect" for o in out)


async def test_disconnect_flushes_a_pending_utterance_and_records_end(env: AgentEnv) -> None:
    await setup(env, "auto")
    voice = await configure_edesy(env)
    session, stt, _tts = _session(env, voice)
    await session.handle_metadata(metadata_frame(to_number=voice.called_number))
    stt.scripted.append(SttResult("haan", 0.9))
    env.provider.responses = [plan("order_status", "answer", None)]
    # caller is mid-utterance (loud, no trailing silence yet) when the socket drops
    await session.handle_audio_chunk(loud_audio(500))
    await session.handle_disconnect()
    assert stt.calls, "the in-flight utterance should still have been transcribed"
    row = (await env.q("SELECT status, end_reason FROM voice_calls"))[0]
    assert row.status == "completed"
