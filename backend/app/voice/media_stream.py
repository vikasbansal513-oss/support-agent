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

import asyncio
import base64
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.speech.providers import SpeechToText, TextToSpeech
from app.voice import service
from app.voice.contracts import E164, INDIAN_MOBILE, VoiceEventIn, VoiceReplyOut
from app.voice.service import Factory, VoiceTenant

logger = logging.getLogger("app.voice.media_stream")

# TEMPORARY DIAGNOSTIC (2026-11-18): counts how many empty-STT-result utterances this
# container instance has dumped raw audio for - see the matching comment in
# _on_utterance below. Per-process, not per-call, on purpose: caps total log volume
# across every call this instance handles, not just one.
_EMPTY_UTTERANCE_DUMPS = 0

# Nov 2026: a real call showed the agent's full "start" turn (STT already skipped on
# this leg, but the LLM call plus TTS) taking ~4.3s end to end (metadata accepted ->
# first audio frame queued) - longer than most callers will silently wait, so the
# caller hangs up right as the real reply becomes ready and it never gets sent (see
# PROGRESS.md P48). Rather than try to make the LLM+TTS round trip itself fast enough
# to feel instant (a much bigger change), we speak a short, fixed filler line the
# instant the call connects - before the slow part even starts - so the caller hears
# something immediately and stays on the line. The filler's own TTS audio is
# synthesized once per (sample_rate, language) and cached in this process for every
# later call, so after the first call it costs ~0ms, not a fresh TTS round trip.
FILLER_TEXT = "नमस्ते, कृपया थोड़ा इंतज़ार करें।"
# ROOT-CAUSE FIX (2026-11-17): the call-start filler above solved the first turn's
# wait, but every *later* turn (customer speaks -> STT -> LLM plan -> DB/handoff work,
# now capped at 12s by app.voice.service._run_agent's TURN_TIMEOUT_S) still went
# completely silent while that ran. Even when it finishes in a normal 2-5s, several
# seconds of dead air after the customer stops talking reads as "this is a bot, not a
# person" (a human agent would say "ek second, dekhti hoon" instead of going quiet) -
# flagged directly from a real call. This is a separate, shorter, non-greeting phrase
# reused for every turn (not just call start), so it must not repeat "नमस्ते" each
# time.
MIDCALL_FILLER_TEXT = "हाँ, एक सेकंड, देख रही हूँ।"
# How long a turn's real processing is given to finish before this filler plays. Low
# enough that the customer doesn't experience the gap as a bot having gone silent
# (human "let me check" reactions land in under ~1.5s), but high enough that a normal
# fast turn (Groq ~1s + DB ~1-2s, confirmed from real external_calls rows) completes
# and speaks its real answer directly, without ever needing the filler at all.
MIDCALL_FILLER_DELAY_S = 1.3
# ROOT-CAUSE FIX (2026-11-17): app.voice.service.TURN_TIMEOUT_S was raised from 12s to
# 16s (so the LLM call's own 6s timeout has room to fail gracefully instead of being
# hard-cancelled mid-flight - see that constant's comment) - but that means a
# genuinely stuck turn can now go up to ~15s past the first filler above with nothing
# further said, which is exactly the "feels like a bot, customer hangs up" risk
# already flagged from a real call. A normal call never reaches this at all (it only
# starts once the first filler has already fired), it just bounds the longest
# possible stretch of true silence to this many seconds instead of the full ceiling.
MIDCALL_FILLER_REPEAT_TEXT = "बस थोड़ा और समय लगेगा।"
MIDCALL_FILLER_REPEAT_S = 5.0
_filler_cache: dict[tuple[str, int, str], bytes] = {}


async def _get_filler_audio(
    tts: TextToSpeech, sample_rate_hz: int, language: str, text: str = FILLER_TEXT
) -> bytes:
    key = (text, sample_rate_hz, language)
    cached = _filler_cache.get(key)
    if cached is not None:
        return cached
    try:
        pcm = await tts.synthesize(text, sample_rate_hz, language)
    except Exception:
        # Best-effort only: if the filler itself fails for any reason, the call
        # continues exactly as before this change (no filler, just a slower first
        # reply) rather than failing the whole turn over a nice-to-have.
        logger.exception("voice-stream: filler audio synthesis failed, skipping filler")
        return b""
    _filler_cache[key] = pcm
    return pcm


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
    # REAL FIX, ROUND 2 (2026-11-17): a real call's own logged VAD stats (jsonPayload,
    # "voice-stream vad stats") confirmed the exact failure, not a guess this time -
    # utterance_ms=540/740, both ending after only 2-11 "loud" frames out of 134-222
    # total. So the first (150.0) threshold round helped some calls (several calls the
    # same day/hour got a non-empty STT result) but was still too high for others -
    # most of this caller's actual speech energy sat BELOW 150, only brief peaks (up to
    # 300-902 max_rms) crossed it, so the bulk of real speech read as "quiet" and the
    # ~500ms end_silence_frames window (meant for a pause BETWEEN words) was reached
    # mid-word/mid-sentence instead, closing the utterance on a fragment.
    # Lowered further (150 -> 90) so quieter voiced speech (not just loud peaks) counts
    # as "speaking", and end_silence_frames raised (25 -> 40, ~800ms) so a normal short
    # pause between words/phrases doesn't end the utterance early either - the two
    # earlier fixes only moved the threshold, never gave speech more room to breathe.
    # Still a reasoned best-effort adjustment, not lab-measured - the same VAD-stats log
    # line will show whether this is enough from the next real call.
    energy_threshold: float = 90.0
    start_frames: int = 2  # consecutive loud frames to confirm speech started
    end_silence_frames: int = 40  # consecutive quiet frames (~800ms @ 20ms) to end
    max_utterance_ms: int = 15_000
    _buffer: bytes = field(default=b"", repr=False)
    _speaking: bool = field(default=False, repr=False)
    _loud_run: int = field(default=0, repr=False)
    _quiet_run: int = field(default=0, repr=False)
    _speech_bytes: bytes = field(default=b"", repr=False)
    # TEMPORARY DIAGNOSTIC (2026-11-17): energy_threshold above was never tuned against
    # real call audio (see class docstring). A real call showed two utterances ending
    # after only ~560-680ms - the minimum possible (start_frames + end_silence_frames),
    # consistent with the threshold being set too high for this caller's actual mic/line
    # level, so real speech reads as "quiet" almost immediately after the loud opening
    # syllable and the utterance is cut short before STT gets enough audio. These counters
    # capture the real RMS levels seen during the cut-short utterance so the threshold can
    # be tuned from real numbers instead of guessed again. Remove once tuned.
    _frame_count: int = field(default=0, repr=False)
    _loud_count: int = field(default=0, repr=False)
    _max_rms: float = field(default=0.0, repr=False)
    last_stats: dict[str, float] | None = field(default=None, repr=False)

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
            rms = _rms(frame)
            loud = rms >= self.energy_threshold
            self._frame_count += 1
            if loud:
                self._loud_count += 1
            self._max_rms = max(self._max_rms, rms)
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
                self.last_stats = {
                    "frames": self._frame_count,
                    "loud_frames": self._loud_count,
                    "max_rms": self._max_rms,
                    "threshold": self.energy_threshold,
                    # TEMPORARY DIAGNOSTIC (2026-11-18): the fraction of this
                    # utterance's frames that were ever above energy_threshold - logged
                    # (not enforced) by EdesyStreamSession.handle_audio_chunk below, to
                    # collect real numbers before trying to tell a noise/echo blip
                    # apart from genuine speech. A previous attempt to enforce a cutoff
                    # here was reverted (see handle_audio_chunk) because it also
                    # discarded genuine speech arriving right after our own TTS reply.
                    "loud_ratio": (
                        self._loud_count / self._frame_count if self._frame_count else 0.0
                    ),
                }
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
        self._frame_count = 0
        self._loud_count = 0
        self._max_rms = 0.0


@dataclass
class OutMessage:
    kind: Literal["audio", "control", "close"]
    data: Any


def _chunk_audio(pcm: bytes, sample_rate_hz: int) -> list[bytes]:
    frame_bytes = int(sample_rate_hz * OUT_FRAME_MS / 1000) * PCM_SAMPLE_WIDTH
    if frame_bytes <= 0:
        return [pcm] if pcm else []
    return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)] if pcm else []


# 2026-11 smoothness fix (user report: real Edesy calls sounded "ruk ruk kar" -
# halting/stop-start). Two separate causes, fixed together:
#   1. The whole reply was synthesized in one synthesize() call, so nothing played
#      until the *entire* reply's audio was ready - on a multi-sentence reply that is
#      a long silent wait, then everything at once.
#   2. Every frame of that one big reply was then sent back-to-back with no pacing
#      (fixed in app.webhooks.voice_stream._send) - a burst, not a steady stream.
# _split_sentences/_chunk_sentences below break a reply into TTS-call-sized pieces so
# _speak() can synthesize and stream them one at a time, prefetching the next piece
# while the current one plays, instead of one big synthesize-then-send-all-at-once.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+")
MIN_CHUNK_CHARS = 40  # group short sentences together before each TTS call, so a tiny
# interjection like "Namaste!" doesn't cost its own synthesize() round trip, while a
# longer reply still starts playing well before the whole thing is done.


def _split_sentences(text: str) -> list[str]:
    """Break speakable()-processed text on a sentence-ending mark (., !, ? or the
    Hindi "।" danda) followed by whitespace. A bare "." with nothing after it (e.g.
    inside a number like "4.3") is left alone, since the split only fires on
    punctuation-then-space - and speakable()'s own _money()/_ref() substitutions never
    leave a literal "." in a spoken amount or order reference anyway."""
    if not text:
        return []
    return [part for part in _SENTENCE_SPLIT.split(text.strip()) if part]


def _chunk_sentences(text: str) -> list[str]:
    """Group _split_sentences()'s output into TTS-call-sized chunks of at least
    MIN_CHUNK_CHARS characters (the last chunk may be shorter). Always returns at
    least one chunk (even [""] for empty text), so callers get exactly the same
    synthesize()-call-count behaviour as before this change when there's nothing to
    split (a one-sentence or punctuation-free reply)."""
    sentences = _split_sentences(text)
    if not sentences:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        current = f"{current} {sentence}".strip() if current else sentence
        if len(current) >= MIN_CHUNK_CHARS:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


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
        # ROOT-CAUSE FIX (2026-11-17), v2: was a plain `_is_playing` bool. Replaced
        # because the receive loop in app.webhooks.voice_stream is a single sequential
        # `while True: await websocket.receive()` - it never reads the socket again
        # until our own handler (and everything it awaits: TTS synthesis, sending our
        # reply) has fully returned. A bool set True-then-False around that one call
        # would already be back to False by the time any audio Edesy sent *during*
        # that window is actually delivered to us - confirmed by this file's own
        # MAX_FRAME_BYTES comment in app.webhooks.voice_stream, which found a real
        # call's frame #2 arriving as one ~3-second buffered chunk, not steady 20ms
        # frames, until Edesy "caught up". A byte count survives that delay correctly:
        # it tracks how many bytes of OUR audio are still "in flight" (being delivered
        # to the line) regardless of when Edesy's matching backlog actually reaches us,
        # and both directions use the same sample_rate_hz/PCM width, so bytes map onto
        # real time 1:1. Decremented in handle_audio_chunk as incoming audio drains it.
        self._mute_bytes_remaining = 0
        self._last_reply: VoiceReplyOut | None = None

    async def handle_metadata(
        self,
        frame: dict[str, Any],
        on_early: Callable[[list[OutMessage]], Awaitable[None]] | None = None,
        on_chunk: Callable[[list[OutMessage]], Awaitable[None]] | None = None,
    ) -> list[OutMessage]:
        """on_early, when given, is awaited with a filler OutMessage batch the moment
        the call is confirmed valid - before the slow LLM+TTS round trip for the real
        reply even starts (see FILLER_TEXT above for why). It is optional and defaults
        to None so existing callers/tests that just inspect this method's own return
        value keep working unchanged and never pay for a filler TTS call.

        on_chunk, when given, is forwarded to _speak() for the real reply so each
        sentence's audio streams out as soon as it's ready instead of waiting for the
        whole reply (see _speak's docstring). Also optional/None for the same reason.
        """
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
        if on_early is not None:
            filler_language = self._last_reply.language if self._last_reply else "hi-IN"
            filler_pcm = await _get_filler_audio(self._tts, self._rate, filler_language)
            filler_frames = _chunk_audio(filler_pcm, self._rate)
            if filler_frames:
                self._mute_bytes_remaining += len(filler_pcm)
                await on_early([OutMessage("audio", frame) for frame in filler_frames])
        event = VoiceEventIn(
            event="start",
            call_id=self.call_id,
            **{"from": self.from_number, "to": self.to_number},
        )
        reply = await service.handle(self._factory, self._voice, event)
        return await self._speak(reply, on_chunk=on_chunk)

    async def handle_audio_chunk(
        self,
        chunk: bytes,
        on_chunk: Callable[[list[OutMessage]], Awaitable[None]] | None = None,
    ) -> list[OutMessage]:
        if self._ended or not self.call_id:
            return []
        out: list[OutMessage] = []

        async def _emit(batch: list[OutMessage]) -> None:
            if on_chunk is not None:
                await on_chunk(batch)
            else:
                out.extend(batch)

        if self._mute_bytes_remaining > 0:
            if (
                self._last_reply is not None
                and self._last_reply.barge_in
                and self._detector.caller_is_speaking(chunk)
            ):
                # Sent via _emit (immediately, through on_chunk when streaming) rather
                # than just appended to `out`, so the provider actually stops playing
                # the old reply's audio before this turn's new reply starts going out -
                # see _speak()'s streaming/prefetch change for why that ordering
                # matters now (previously everything for one reply left together in
                # one batch, so ordering here didn't matter as much).
                await _emit([OutMessage("control", {"type": "killAudio"})])
                # Caller is genuinely talking already - stop muting and let this same
                # chunk fall through to the detector below instead of discarding it,
                # so the start of their speech isn't lost.
                self._mute_bytes_remaining = 0
            else:
                # ROOT-CAUSE FIX (2026-11-17), v2: see _mute_bytes_remaining's
                # docstring-comment in __init__ for why this is a byte budget, not a
                # bool. Whatever Edesy sends back on the inbound side while our own
                # reply/greeting audio is still being delivered to the line (echo or
                # leakage of it, without proper echo cancellation, is common on
                # VoIP/PSTN gateways) was being read as "the caller is speaking" -
                # that matches the repeated real-call evidence in UtteranceDetector's
                # docstring below: a brief "loud" burst right as our own audio stops,
                # then near-total silence, closing the utterance at (or just past)
                # the theoretical minimum length, regardless of what
                # energy_threshold was tuned to, because the detector was never
                # really evaluating the caller's own speech in the first place.
                # Drain the budget from the front of this chunk and discard that
                # part; if this chunk is bigger than what's left owed (e.g. Edesy's
                # own buffering caught up and handed us one combined chunk that also
                # contains genuinely new audio past our own playback), push the
                # leftover tail through to the detector below instead of losing it.
                discard = min(len(chunk), self._mute_bytes_remaining)
                self._mute_bytes_remaining -= discard
                chunk = chunk[discard:]
                if not chunk:
                    return out
        utterance = self._detector.push(chunk)
        if utterance is None:
            return out
        # TEMPORARY DIAGNOSTIC (2026-11-17): see UtteranceDetector.last_stats - logs the
        # real RMS energy levels seen for this utterance so energy_threshold can be tuned
        # from real numbers. Remove once tuned.
        stats = self._detector.last_stats
        if stats is not None:
            logger.info(
                "voice-stream vad stats: frames=%d loud_frames=%d max_rms=%.1f threshold=%.1f "
                "loud_ratio=%.2f",
                stats["frames"],
                stats["loud_frames"],
                stats["max_rms"],
                stats["threshold"],
                stats["loud_ratio"],
            )
            # REVERTED (2026-11-18): an earlier version of this block skipped STT below
            # min_loud_ratio, based on one real-call dump that looked like a brief
            # noise/echo edge followed by silence. A local run of
            # tests/voice/test_media_stream.py::test_audio_turn_runs_stt_then_agent_then_tts
            # proved that signature is NOT specific to noise/echo: genuine caller speech
            # arriving right after our own TTS reply gets its leading portion eaten by
            # the _mute_bytes_remaining drain above (by design, to reject our own
            # echo/leakage), so only a short tail of real speech reaches this detector -
            # same low loud_ratio shape as an actual blip. Skipping STT here would have
            # silently dropped genuine replies spoken right after the agent talks, so the
            # skip was removed; loud_ratio is still logged above for tuning, it just no
            # longer blocks anything. VERIFY: a real fix needs real-call evidence that
            # separates "truly no speech" from "speech partly eaten by our own mute
            # budget" - don't reintroduce a skip here without that.
        out.extend(await self._on_utterance(utterance, on_chunk=on_chunk))
        return out

    async def _on_utterance(
        self,
        pcm: bytes,
        on_chunk: Callable[[list[OutMessage]], Awaitable[None]] | None = None,
    ) -> list[OutMessage]:
        if not self.call_id or not self.from_number or not self.to_number:
            return []  # metadata frame never arrived; nothing sane to do
        language = self._last_reply.language if self._last_reply else "hi-IN"
        utterance_ms = int(len(pcm) / (self._rate * PCM_SAMPLE_WIDTH) * 1000)
        result = await self._stt.transcribe(pcm, self._rate, language)
        # TEMPORARY DIAGNOSTIC (2026-11-17): user report - customer sometimes gets no
        # reply at all on a real call, and the dashboard later shows a
        # SILENCE_NO_RESPONSE handoff even though the customer insists they were
        # talking. app.voice.service._turn() only counts a turn as "silence" when
        # event.speech is empty - which happens here whenever Google STT returns an
        # empty transcript for whatever audio the VAD (UtteranceDetector) decided was
        # one utterance. Neither the audio length the VAD captured nor what STT
        # actually returned was ever logged before this, so there was no way to tell
        # "VAD never heard anything" apart from "VAD heard something but STT failed on
        # it" apart from "STT worked but something later dropped it". Logging length +
        # confidence only (never the transcript text itself - that's customer data,
        # kept out of logs per this app's own redaction rules) narrows that down from
        # the next real call. Remove once the real cause is confirmed and fixed.
        logger.info(
            "voice-stream stt result: utterance_ms=%d pcm_bytes=%d text_empty=%s confidence=%s",
            utterance_ms,
            len(pcm),
            not result.text.strip(),
            result.confidence,
        )
        # TEMPORARY DIAGNOSTIC (2026-11-18): two independent STT providers (Google,
        # then Sarvam) both return an empty transcript on audio the VAD measured as
        # genuinely loud (see UtteranceDetector's vad-stats log) - ruling out a
        # single provider's bug. The one thing not yet ruled out is whether the
        # "loud" audio UtteranceDetector is capturing is the caller's real speech at
        # all, versus line noise/ring-back/echo of our own TTS leaking back in
        # (flagged as a real risk in _mute_bytes_remaining's own docstring above) -
        # a purely energy-based VAD cannot tell those apart, only an STT or a human
        # listening to the actual bytes can. Logging the raw audio as base64 (NOT the
        # transcript - there isn't one) for the first 3 empty-result utterances per
        # container instance only, so it can be decoded and inspected/played back
        # once to settle this, instead of guessing again. Remove once confirmed.
        global _EMPTY_UTTERANCE_DUMPS
        if not result.text.strip() and _EMPTY_UTTERANCE_DUMPS < 3:
            _EMPTY_UTTERANCE_DUMPS += 1
            logger.info(
                "voice-stream empty-utterance audio dump #%d: rate=%d pcm_b64=%s",
                _EMPTY_UTTERANCE_DUMPS,
                self._rate,
                base64.b64encode(pcm).decode("ascii"),
            )
        # FIX (2026-11-17): on this (Edesy) path, _on_utterance only ever runs after
        # UtteranceDetector has already decided the caller WAS speaking (that's what
        # "utterance" means here - see push()'s state machine) - so an empty `result.
        # text` below means STT failed on real captured audio, never that the caller
        # said nothing. Passing that to app.voice.service._turn() as plain empty
        # speech used to make it indistinguishable from true provider-reported silence
        # (Plivo/generic's own "caller went quiet" event) - the customer, who just
        # spoke, would hear "are you still there?" instead of "I couldn't hear that
        # clearly, please repeat", and after a couple of these would be handed off as
        # SILENCE_NO_RESPONSE ("no response") even though they never stopped talking.
        # heard_unclear tells _turn() which of the two actually happened.
        event = VoiceEventIn(
            event="turn",
            call_id=self.call_id,
            **{"from": self.from_number, "to": self.to_number},
            speech=result.text[:2000],
            confidence=result.confidence,
            heard_unclear=not result.text.strip(),
        )
        # ROOT-CAUSE FIX (2026-11-17): see MIDCALL_FILLER_TEXT's comment above. Start
        # the real (possibly slow, up to 12s) agent call in the background and race it
        # against a short deadline - a normal fast turn finishes first and this never
        # plays anything extra; a slow turn gets bridged with a brief spoken
        # acknowledgement instead of silence, then still speaks the real answer once
        # it's ready (the filler does not replace the reply, it only covers the wait).
        handle_task: asyncio.Task[VoiceReplyOut] = asyncio.ensure_future(
            service.handle(self._factory, self._voice, event)
        )
        done, _ = await asyncio.wait({handle_task}, timeout=MIDCALL_FILLER_DELAY_S)
        if handle_task not in done and on_chunk is not None:
            filler_pcm = await _get_filler_audio(
                self._tts, self._rate, language, MIDCALL_FILLER_TEXT
            )
            filler_frames = _chunk_audio(filler_pcm, self._rate)
            if filler_frames:
                self._mute_bytes_remaining += len(filler_pcm)
                await on_chunk([OutMessage("audio", frame) for frame in filler_frames])
            # See MIDCALL_FILLER_REPEAT_S above: bounds how long true silence can run
            # if the turn is still not done after the first filler.
            while handle_task not in done:
                done, _ = await asyncio.wait({handle_task}, timeout=MIDCALL_FILLER_REPEAT_S)
                if handle_task in done:
                    break
                repeat_pcm = await _get_filler_audio(
                    self._tts, self._rate, language, MIDCALL_FILLER_REPEAT_TEXT
                )
                repeat_frames = _chunk_audio(repeat_pcm, self._rate)
                if repeat_frames:
                    self._mute_bytes_remaining += len(repeat_pcm)
                    await on_chunk([OutMessage("audio", frame) for frame in repeat_frames])
        reply = await handle_task
        return await self._speak(reply, on_chunk=on_chunk)

    async def _speak(
        self,
        reply: VoiceReplyOut,
        on_chunk: Callable[[list[OutMessage]], Awaitable[None]] | None = None,
    ) -> list[OutMessage]:
        """Synthesize and (optionally) stream reply.say's audio, sentence by sentence.

        2026-11 smoothness fix: previously this made one synthesize() call for the
        *entire* reply, then returned every resulting frame in one batch - meaning
        nothing played until the whole reply's TTS was done, and (once
        app.webhooks.voice_stream._send paces frames) a multi-sentence reply would
        have gone out as one long first-sentence-sized wait followed by everything at
        once. Each chunk from _chunk_sentences() is synthesized and handed to
        on_chunk - and therefore sent, paced, by the websocket layer - as soon as it's
        ready.

        ROOT-CAUSE FIX (2026-11-18): the previous version of this streaming change
        only started the *next* chunk's synthesize() call while the *current* chunk's
        audio was being emitted (one chunk's head start at a time) - which only hides
        TTS latency when a chunk's own playback time is longer than the next chunk's
        synthesize() call. Real call logs (Cloud Run, support-agent service,
        2026-11-17/18) showed each google TTS synthesize() call for a short chunk of
        this app's own Hindi replies taking ~4-5s (vs Google's normal 185-360ms,
        already flagged as an open issue in app.webhooks.voice_stream's
        http_tts/ExternalHttp comment) - far longer than that same short chunk's own
        playback time, so the one-chunk head start was never enough: a 3-chunk, 182-
        character greeting measurably took ~13s of real end-to-end silence (reply
        logged at 02:27:16, tts output not logged until 02:27:29 the same call),
        because each chunk's synthesize() call only started after the previous one
        had already returned - the chunks' slow network latencies summed instead of
        overlapping.

        Fired every chunk's synthesize() call at once instead (all `tasks` below start
        together, before any audio is sent), so their latencies run concurrently and
        the total wait for the *first* chunk to be ready is roughly the slowest single
        chunk's latency, not the sum of all of them - cutting the measured ~13s greeting
        gap to roughly one chunk's worth (~4-5s going by the same real-call numbers).
        This does not make any individual Google TTS call faster (that latency is on
        Google's side, outside this app's control) - it only stops this app's own code
        from needlessly serializing calls that could run in parallel. Chunks are still
        emitted (and therefore paced/played) strictly in reply order, same as before.

        When on_chunk is None (unit tests exercising this method directly, as before
        this change), every frame is instead collected into the returned list - no
        behaviour change for existing callers/tests, just no streaming/pacing benefit
        (there's no real socket to stream to in that case anyway).
        """
        self._last_reply = reply
        out: list[OutMessage] = []

        async def _emit(batch: list[OutMessage]) -> None:
            if on_chunk is not None:
                await on_chunk(batch)
            else:
                out.extend(batch)

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
        lang = _reply_language(reply)
        chunks = _chunk_sentences(reply.say)  # _chunk_sentences always returns >=1 chunk
        total_pcm_bytes = 0
        # ROOT-CAUSE FIX (2026-11-18): start every chunk's synthesize() call
        # concurrently up front (instead of only the next one, one at a time - see this
        # method's docstring) so their network latencies overlap rather than sum.
        # Awaited below strictly in order so chunks are still emitted/paced in reply
        # order even though they may finish out of order.
        tasks: list[asyncio.Task[bytes]] = [
            asyncio.create_task(self._tts.synthesize(chunk, self._rate, lang)) for chunk in chunks
        ]
        for task in tasks:
            pcm = await task
            total_pcm_bytes += len(pcm) if pcm else 0
            frames = _chunk_audio(pcm, self._rate)
            if frames:
                await _emit([OutMessage("audio", frame) for frame in frames])
        # ROOT-CAUSE FIX (2026-11-17), v2: add this reply's bytes to the mute budget
        # (see _mute_bytes_remaining's docstring-comment in __init__) rather than
        # toggling a bool - handle_audio_chunk's gate drains it as real inbound audio
        # (whenever it actually arrives, backlog or not) comes in, so real listening
        # resumes exactly after roughly this much time's worth of audio has passed,
        # not at the (unreliable) moment this coroutine happens to return.
        self._mute_bytes_remaining += total_pcm_bytes
        logger.info("voice-stream tts output: pcm_bytes=%d", total_pcm_bytes)
        if reply.action == "hangup":
            self._ended = True
            await _emit([OutMessage("control", {"type": "disconnect"})])
        elif reply.action == "transfer":
            # Edesy's Media Streams protocol (as documented) has no bot->platform
            # "bridge this call to another number" control message - only killAudio
            # and disconnect. A real warm transfer would need a separate Edesy REST
            # call, which is not confirmed. For now we speak the reply (which already
            # says a human will call back / connect, per app.voice.speech templates)
            # and disconnect, same as the "callback" outcome on the text-based path.
            # VERIFY: confirm with Edesy whether Media Streams supports live transfer.
            self._ended = True
            await _emit([OutMessage("control", {"type": "disconnect"})])
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
