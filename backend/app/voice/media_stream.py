"""Edesy Media Streams (DIY Voice Calling Agent Stack, raw-audio mode).

Every other voice provider in this app (Plivo, generic, fake_ivr - app.voice.providers)
does its own speech-to-text and text-to-speech and only ever exchanges *text* with us
over a synchronous webhook (app.webhooks.voice). That was a deliberate design choice
(DECISIONS.md P17) specifically to avoid building our own audio pipeline.

Edesy's "Media Streams" mode is the one case where the provider instead hands us raw
caller audio over a WebSocket and expects audio back - so this module is the one place
in the app that runs its own turn-detection (voice-activity "VAD") and calls out to
app.speech.providers for STT/TTS. Everything downstream of a transcribed utterance
(policy, handoffs, billing, the LLM agent) is untouched: we build a normal
VoiceEventIn and hand it to app.voice.service.handle(), exactly like the webhook path
does, and get back a normal VoiceReplyOut.

No DB commit, no LLM call, no send happens directly in this module - app.voice.service
and the actions engine still own all of that, same rule as the agent graph.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.speech.providers import SpeechToText, TextToSpeech
from app.voice import service
from app.voice.contracts import E164, INDIAN_MOBILE, VoiceEventIn, VoiceReplyOut
from app.voice.service import Factory, VoiceTenant

logger = logging.getLogger("app.voice.media_stream")

# Providers whose calls arrive over this raw-audio WebSocket path rather than the
# text-based webhook in app.webhooks.voice / app.voice.providers. Referenced by
# app.voice.contracts to accept these names alongside the text-provider registry.
STREAM_PROVIDERS = ("edesy",)

PCM_SAMPLE_WIDTH = 2  # 16-bit signed PCM, Edesy's documented Media Streams format
OUT_FRAME_MS = 20  # chunk outgoing audio into ~20ms frames; not mandated by Edesy's
# docs for this product, but matches telephony convention and the frame size the
# related Edesy "SIP trunk for AI voice agents" product documents - safe default.
# VERIFY against a real Edesy account whether any particular outgoing chunk size is
# required or just "some" binary frames are acceptable.


def _normalize_e164(raw: str) -> str:
    """Best-effort normalization of a telephony-provider phone number into the
    E.164 shape app.voice.contracts.VoiceEventIn requires (+91XXXXXXXXXX).

    ROOT CAUSE (Nov 2026): Edesy's Media Streams metadata frame was found (via
    the temporary diagnostic logging in app.webhooks.voice_stream._handle_text)
    to crash VoiceEventIn's "start" event with 2 validation errors - from_number
    and to_number both failed the strict E.164 check below, because this path
    never normalized the raw "from"/"to" values before this fix. That crash was
    silently caught by the generic `except Exception` in voice_stream_webhook,
    so service.handle() - the actual agent/LLM reply logic - was never reached
    on any call, which is why the agent never replied. Every other provider in
    this app (WhatsApp/Gupshup) already delivers E.164 numbers, so no normalizer
    was needed before this one.

    Mirrors the existing bare-number handling in
    app.voice.contracts.PublicNumberIn._indian_mobile (strip +91/91/leading 0,
    then re-add +91) rather than inventing a new convention.
    VERIFY: only covers Indian 10-digit mobile numbers, same scope as that
    existing validator; a non-Indian "from" number would still be left as-is
    and rejected by VoiceEventIn's own validator with a clear error.
    """
    cleaned = re.sub(r"[\s\-()]", "", raw or "")
    if not cleaned:
        return cleaned
    if E164.match(cleaned):
        return cleaned
    bare = re.sub(r"^(\+91|91|0)", "", cleaned)
    if INDIAN_MOBILE.match(bare):
        return "+91" + bare
    return cleaned  # leave as-is; VoiceEventIn's own validator will reject it clearly


def _rms(frame: bytes) -> float:
    if not frame:
        return 0.0
    total = 0
    count = len(frame) // 2
    if count == 0:
        return 0.0
    for i in range(0, count * 2, 2):
        sample = int.from_bytes(frame[i : i + 2], "little", signed=True)
        total += sample * sample
    return float((total / count) ** 0.5)


@dataclass
class UtteranceDetector:
    """Simple energy-based endpointing over 16-bit PCM audio.

    Edesy's Media Streams protocol streams continuous raw audio both ways with no
    "caller stopped talking" event of its own (unlike the text-based providers, where
    the telephony side's own STT engine decides utterance boundaries and posts us a
    finished transcript per turn). We have to decide that boundary ourselves here.

    This is a basic amplitude threshold, not a trained voice-activity-detector model -
    deliberately so, to avoid adding a new ML dependency for a first cut. VERIFY:
    threshold/frame-count defaults below are not tuned against real call audio or
    background noise; tune from real recordings once available (same spirit as the
    STUCK_THRESHOLD_HOURS/cluster-window defaults elsewhere in this codebase that were
    taken from the spec's own example and flagged for later tuning).
    """

    sample_rate_hz: int
    frame_ms: int = 20
    energy_threshold: float = 500.0
    start_frames: int = 2  # consecutive loud frames to confirm speech started
    end_silence_frames: int = 25  # consecutive quiet frames (~500ms @ 20ms) to end
    max_utterance_ms: int = 15_000

    _buffer: bytes = field(default=b"", repr=False)
    _speaking: bool = field(default=False, repr=False)
    _loud_run: int = field(default=0, repr=False)
    _quiet_run: int = field(default=0, repr=False)
    _speech_bytes: bytes = field(default=b"", repr=False)

    @property
    def _frame_bytes(self) -> int:
        return int(self.sample_rate_hz * self.frame_ms / 1000) * PCM_SAMPLE_WIDTH

    @property
    def _max_bytes(self) -> int:
        return int(self.sample_rate_hz * self.max_utterance_ms / 1000) * PCM_SAMPLE_WIDTH

    def is_loud(self, frame: bytes) -> bool:
        return _rms(frame) >= self.energy_threshold

    def push(self, chunk: bytes) -> bytes | None:
        """Feed incoming audio. Returns a completed utterance's PCM bytes once the
        caller has gone quiet after speaking, else None.
        """
        self._buffer += chunk
        frame_size = self._frame_bytes
        if frame_size <= 0:
            return None
        utterance: bytes | None = None
        while len(self._buffer) >= frame_size:
            frame, self._buffer = self._buffer[:frame_size], self._buffer[frame_size:]
            loud = self.is_loud(frame)
            if not self._speaking:
                if loud:
                    self._loud_run += 1
                    self._speech_bytes += frame
                    if self._loud_run >= self.start_frames:
                        self._speaking = True
                        self._quiet_run = 0
                else:
                    self._loud_run = 0
                    self._speech_bytes = b""
                continue
            self._speech_bytes += frame
            if loud:
                self._quiet_run = 0
            else:
                self._quiet_run += 1
            if (
                self._quiet_run >= self.end_silence_frames
                or len(self._speech_bytes) >= self._max_bytes
            ):
                utterance = self._speech_bytes
                self._reset()
        return utterance

    def flush(self) -> bytes | None:
        """Force-end whatever utterance is in progress (call ending mid-speech)."""
        if self._speaking and self._speech_bytes:
            out = self._speech_bytes
            self._reset()
            return out
        return None

    def caller_is_speaking(self, chunk: bytes) -> bool:
        """Best-effort barge-in probe: is this incoming chunk loud, independent of the
        endpointing state machine above. Used only while we're playing our own TTS
        audio back, to decide whether to cut it off.
        """
        frame_size = self._frame_bytes
        if frame_size <= 0 or len(chunk) < frame_size:
            return False
        return self.is_loud(chunk[:frame_size])

    def _reset(self) -> None:
        self._speaking = False
        self._loud_run = 0
        self._quiet_run = 0
        self._speech_bytes = b""


@dataclass
class OutMessage:
    kind: Literal["audio", "control", "close"]
    data: Any


def _chunk_audio(pcm: bytes, sample_rate_hz: int) -> list[bytes]:
    frame_bytes = int(sample_rate_hz * OUT_FRAME_MS / 1000) * PCM_SAMPLE_WIDTH
    if frame_bytes <= 0:
        return [pcm] if pcm else []
    return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)] if pcm else []


def _reply_language(reply: VoiceReplyOut) -> str:
    return reply.language


class EdesyStreamSession:
    """One Edesy Media Streams WebSocket connection = one call. Transport-agnostic on
    purpose (app.webhooks.voice_stream is the thin FastAPI WebSocket shim that feeds
    this class and writes its OutMessages to the wire) so the turn-taking logic can be
    exercised in tests without a real socket.
    """

    def __init__(
        self,
        factory: Factory,
        voice: VoiceTenant,
        stt: SpeechToText,
        tts: TextToSpeech,
        sample_rate_hz: int,
    ) -> None:
        self._factory = factory
        self._voice = voice
        self._stt = stt
        self._tts = tts
        self._rate = sample_rate_hz
        self._detector = UtteranceDetector(sample_rate_hz=sample_rate_hz)
        self.call_id: str | None = None
        self.from_number: str | None = None
        self.to_number: str | None = None
        self._ended = False
        self._is_playing = False
        self._last_reply: VoiceReplyOut | None = None

    async def handle_metadata(self, frame: dict[str, Any]) -> list[OutMessage]:
        self.call_id = str(
            frame.get("callSid") or frame.get("call_sid") or frame.get("callId") or ""
        )
        self.from_number = _normalize_e164(str(frame.get("from") or ""))
        self.to_number = _normalize_e164(str(frame.get("to") or self._voice.called_number))
        if not self.call_id or not self.from_number:
            # TEMPORARY DIAGNOSTIC (Nov 2026): see the matching note in
            # app.webhooks.voice_stream._handle_text - this close used to happen
            # silently with no log line anywhere, which is why a bad metadata frame
            # shape looked like "call connects, caller talks, nothing happens" with
            # nothing in the logs. Remove alongside that note once Edesy's real frame
            # shape is confirmed.
            logger.warning(
                "voice-stream metadata frame missing call_id/from - closing. frame=%s",
                frame,
            )
            return [OutMessage("close", None)]
        if not E164.match(self.from_number) or not E164.match(self.to_number):
            # Normalization in _normalize_e164 above couldn't make this E.164 (e.g. a
            # non-Indian number, or a shape not covered yet) - log it plainly so the
            # next call doesn't need another diagnostic-logging redeploy to see why.
            logger.warning(
                "voice-stream from/to still not E.164 after normalization: from=%s to=%s",
                self.from_number,
                self.to_number,
            )
        event = VoiceEventIn(
            event="start", call_id=self.call_id, **{"from": self.from_number, "to": self.to_number}
        )
        reply = await service.handle(self._factory, self._voice, event)
        return await self._speak(reply)

    async def handle_audio_chunk(self, chunk: bytes) -> list[OutMessage]:
        if self._ended or not self.call_id:
            return []
        out: list[OutMessage] = []
        if self._is_playing and self._last_reply is not None and self._last_reply.barge_in:
            if self._detector.caller_is_speaking(chunk):
                out.append(OutMessage("control", {"type": "killAudio"}))
                self._is_playing = False
        utterance = self._detector.push(chunk)
        if utterance is None:
            return out
        return out + await self._on_utterance(utterance)

    async def _on_utterance(self, pcm: bytes) -> list[OutMessage]:
        if not self.call_id or not self.from_number or not self.to_number:
            return []  # metadata frame never arrived; nothing sane to do
        language = self._last_reply.language if self._last_reply else "hi-IN"
        result = await self._stt.transcribe(pcm, self._rate, language)
        event = VoiceEventIn(
            event="turn",
            call_id=self.call_id,
            **{"from": self.from_number, "to": self.to_number},
            speech=result.text[:2000],
            confidence=result.confidence,
        )
        reply = await service.handle(self._factory, self._voice, event)
        return await self._speak(reply)

    async def _speak(self, reply: VoiceReplyOut) -> list[OutMessage]:
        self._last_reply = reply
        out: list[OutMessage] = []
        # reply.say is already run through app.voice.speech.speakable() upstream in
        # app.voice.service (same as every other provider's render()) - speak it as-is.
        # TEMPORARY DIAGNOSTIC (Nov 2026): the earlier VoiceEventIn crash (see
        # _normalize_e164's docstring) is fixed, but calls still produce no audible
        # reply. This logs whether the agent actually returned text and whether TTS
        # actually produced bytes, so the next failure points at the exact stage
        # (empty agent reply vs. TTS returning nothing vs. a format Edesy can't play)
        # instead of needing another guess-and-redeploy round. Remove once resolved.
        logger.info(
            "voice-stream reply: say_len=%d action=%s rate=%d lang=%s",
            len(reply.say),
            reply.action,
            self._rate,
            _reply_language(reply),
        )
        pcm = await self._tts.synthesize(reply.say, self._rate, _reply_language(reply))
        logger.info("voice-stream tts output: pcm_bytes=%d", len(pcm) if pcm else 0)
        frames = _chunk_audio(pcm, self._rate)
        if frames:
            self._is_playing = True
            out.extend(OutMessage("audio", frame) for frame in frames)
        if reply.action == "hangup":
            self._ended = True
            out.append(OutMessage("control", {"type": "disconnect"}))
        elif reply.action == "transfer":
            # Edesy's Media Streams protocol (as documented) has no bot->platform
            # "bridge this call to another number" control message - only killAudio
            # and disconnect. A real warm transfer would need a separate Edesy REST
            # call, which is not confirmed. For now we speak the reply (which already
            # says a human will call back / connect, per app.voice.speech templates)
            # and disconnect, same as the "callback" outcome on the text-based path.
            # VERIFY: confirm with Edesy whether Media Streams supports live transfer.
            self._ended = True
            out.append(OutMessage("control", {"type": "disconnect"}))
        return out

    async def handle_disconnect(self) -> None:
        if self._ended or not self.call_id:
            return
        leftover = self._detector.flush()
        if leftover:
            await self._on_utterance(leftover)
        try:
            # BUGFIX (Nov 2026): VoiceEventIn(...) construction used to sit outside
            # this try/except, so an invalid from_number/to_number (see
            # _normalize_e164's docstring) raised a pydantic ValidationError straight
            # out of the `finally: await session.handle_disconnect()` block in
            # voice_stream_webhook - unhandled, since that's outside its own
            # try/except, which is why it showed up as a bare traceback in the logs
            # instead of a "voice stream end-event failed" line. Building the event is
            # now covered by the same best-effort try as the service.handle() call.
            event = VoiceEventIn(
                event="end",
                call_id=self.call_id,
                **{
                    "from": self.from_number or "",
                    "to": self.to_number or self._voice.called_number,
                },
                reason="caller_disconnect",
            )
            await service.handle(self._factory, self._voice, event)
        except Exception:  # pragma: no cover - best-effort cleanup, call is gone anyway
            logger.exception("voice stream end-event failed")
        self._ended = True
