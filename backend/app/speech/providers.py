"""Speech-to-text / text-to-speech providers for the Edesy Media Streams (raw-audio)
voice path. Call-routing/webhook mapping for the existing turn-based providers (Plivo,
generic) stays untouched in app.voice.providers — those providers do their own STT/TTS
and only ever exchange text with us. This module is only used by app.voice.media_stream,
which is the one voice path where WE run STT/TTS, because Edesy's Media Streams mode
hands us raw audio instead of transcribed text.

No LLM/agent logic lives here - this is call-routing-adjacent plumbing only, same rule
as app.voice.providers.
"""

import base64
import logging
from typing import Protocol

from app.integrations.http import ExternalHttp, UpstreamError

logger = logging.getLogger("app.speech.providers")

STT_PROVIDERS = ("google", "sarvam")
TTS_PROVIDERS = ("google", "sarvam")


# Google Text-to-Speech's LINEAR16 response is a WAV file (RIFF header + fmt/data
# sub-chunks), not raw PCM. Edesy Media Streams wants raw L16 PCM frames only, so we
# strip the header. A canonical 16-bit PCM WAV header from this API is 44 bytes; we
# scan for the "data" sub-chunk instead of assuming a fixed offset, in case extra
# sub-chunks are present.
def _strip_wav_header(wav_bytes: bytes) -> bytes:
    marker = wav_bytes.find(b"data")
    if marker == -1 or marker + 8 > len(wav_bytes):
        return wav_bytes
    return wav_bytes[marker + 8 :]


# 2026-11-18: the reverse of _strip_wav_header above - app.voice.media_stream's VAD
# hands us raw 16-bit PCM (no container), but Sarvam's /speech-to-text REST endpoint
# (confirmed from Sarvam's own API docs, docs.sarvam.ai) takes a multipart audio
# *file* and documents WAV (16-bit PCM) as the preferred/most-accurate format - so
# the raw PCM needs a standard WAV header wrapped around it before upload. This is a
# plain, canonical 44-byte PCM WAV header (no extra sub-chunks), the same shape
# Google's own TTS response already uses (see _strip_wav_header above).
def _make_wav_header(pcm_bytes: bytes, sample_rate_hz: int) -> bytes:
    channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate_hz * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_bytes)
    header = b"RIFF"
    header += (36 + data_size).to_bytes(4, "little")
    header += b"WAVEfmt "
    header += (16).to_bytes(4, "little")  # fmt chunk size
    header += (1).to_bytes(2, "little")  # audio format: PCM
    header += channels.to_bytes(2, "little")
    header += sample_rate_hz.to_bytes(4, "little")
    header += byte_rate.to_bytes(4, "little")
    header += block_align.to_bytes(2, "little")
    header += bits_per_sample.to_bytes(2, "little")
    header += b"data"
    header += data_size.to_bytes(4, "little")
    return header + pcm_bytes


class SttResult:
    def __init__(self, text: str, confidence: float | None) -> None:
        self.text = text
        self.confidence = confidence


class SpeechToText(Protocol):
    name: str

    async def transcribe(
        self, pcm16_audio: bytes, sample_rate_hz: int, language: str
    ) -> SttResult: ...


class TextToSpeech(Protocol):
    name: str

    async def synthesize(self, text: str, sample_rate_hz: int, language: str) -> bytes: ...


_LANG_MAP = {"hi-IN": "hi-IN", "en-IN": "en-IN", "hinglish": "hi-IN"}


def _google_language(language: str) -> str:
    return _LANG_MAP.get(language, "en-IN")


class GoogleSpeechToText:
    """Google Cloud Speech-to-Text v1, synchronous `speech:recognize`.

    We buffer each caller utterance locally (app.voice.media_stream's VAD decides where
    an utterance starts/ends) and send it as one blocking recognize call - simpler and
    far less risky than driving Google's bidirectional streaming/gRPC API, and the one
    turn-at-a-time shape already matches how app.voice.service.handle() works for every
    other provider.
    """

    name = "google"

    def __init__(self, http: ExternalHttp, api_key: str) -> None:
        self._http = http
        self._key = api_key

    async def transcribe(self, pcm16_audio: bytes, sample_rate_hz: int, language: str) -> SttResult:
        if not pcm16_audio:
            return SttResult("", None)
        body = {
            "config": {
                "encoding": "LINEAR16",
                "sampleRateHertz": sample_rate_hz,
                "languageCode": _google_language(language),
                "enableAutomaticPunctuation": True,
                # Nov 2026: every real call so far has failed STT outright with
                # "Invalid recognition 'config': The phone_call model is currently
                # not supported for language: hi-IN" - Google's enhanced "phone_call"
                # model (tuned for 8kHz telephone audio) is only available for a
                # short, mostly-English list of languages, and the two this app
                # actually uses (hi-IN, en-IN - see _LANG_MAP above) are not on it,
                # so every single transcription request was rejected before Google
                # even looked at the audio. Dropping model/useEnhanced falls back to
                # Google's default recognition model, which has no such per-language
                # allowlist - slightly less tuned for phone audio, but it is the
                # difference between "never transcribes anything" and "works".
            },
            "audio": {"content": base64.b64encode(pcm16_audio).decode("ascii")},
        }
        try:
            response = await self._http.request(
                "POST",
                "https://speech.googleapis.com/v1/speech:recognize",
                operation="stt_recognize",
                params={"key": self._key},
                json=body,
            )
        except UpstreamError:
            return SttResult("", None)
        if response.status_code != 200:
            # TEMPORARY DIAGNOSTIC (Nov 2026): every real call so far has gotten a 400
            # from this endpoint with nothing logged beyond the status code, so there
            # has been no way to tell apart a bad API key, a disabled API, or a
            # malformed request body. Google's error JSON names the exact reason
            # (error.message / error.status) - log it so the next failure is
            # diagnosable from one log line instead of another guess-and-redeploy
            # round. Remove once STT is confirmed working end-to-end.
            logger.error(
                "google STT non-200: status=%d body=%s",
                response.status_code,
                response.text[:1000],
            )
            return SttResult("", None)
        try:
            data = response.json()
        except ValueError:
            return SttResult("", None)
        results = data.get("results") or []
        if not results:
            return SttResult("", None)
        alternatives = results[0].get("alternatives") or []
        if not alternatives:
            return SttResult("", None)
        best = alternatives[0]
        transcript = str(best.get("transcript") or "").strip()
        confidence = best.get("confidence")
        return SttResult(transcript, float(confidence) if confidence is not None else None)


class GoogleTextToSpeech:
    """Google Cloud Text-to-Speech v1 `text:synthesize`, LINEAR16 output at the
    call's sample rate. Returns raw PCM (WAV header stripped) ready to frame and
    stream back to Edesy.
    """

    name = "google"

    def __init__(self, http: ExternalHttp, api_key: str) -> None:
        self._http = http
        self._key = api_key

    async def synthesize(self, text: str, sample_rate_hz: int, language: str) -> bytes:
        if not text.strip():
            return b""
        body = {
            "input": {"text": text},
            "voice": {"languageCode": _google_language(language), "ssmlGender": "NEUTRAL"},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": sample_rate_hz},
        }
        try:
            response = await self._http.request(
                "POST",
                "https://texttospeech.googleapis.com/v1/text:synthesize",
                operation="tts_synthesize",
                params={"key": self._key},
                json=body,
            )
        except UpstreamError:
            return b""
        if response.status_code != 200:
            # TEMPORARY DIAGNOSTIC (Nov 2026): see the matching note in
            # GoogleSpeechToText.transcribe above - same situation, same fix. Remove
            # once TTS is confirmed working end-to-end.
            logger.error(
                "google TTS non-200: status=%d body=%s",
                response.status_code,
                response.text[:1000],
            )
            return b""
        try:
            data = response.json()
        except ValueError:
            return b""
        encoded = data.get("audioContent")
        if not encoded:
            return b""
        try:
            wav_bytes = base64.b64decode(encoded)
        except (ValueError, TypeError):
            return b""
        return _strip_wav_header(wav_bytes)


class SarvamSpeechToText:
    """Sarvam AI Speech-to-Text (`saaras:v3`), REST `/speech-to-text`.

    Built 2026-11-18 as an alternative to GoogleSpeechToText above, after real calls
    repeatedly showed Google returning an empty transcript on audio our own VAD
    confirmed was genuine, clearly-loud speech (see app.voice.media_stream's VAD
    stats logging) - a failure mode with no further diagnosis possible without
    Google's own raw (undocumented) empty-result reason, and no fix confirmed on
    Google's side. Uses a separate API key from TTS (app.speech.runtime.
    EffectiveSpeech.stt_api_key / migration 0041) since this is a different provider
    account, not just a different model from the same one.

    Per Sarvam's own REST API docs (docs.sarvam.ai/api-reference-docs/api-guides-
    tutorials/speech-to-text/rest-api): POST https://api.sarvam.ai/speech-to-text,
    multipart/form-data, header `api-subscription-key`, form fields `file` (audio),
    `model` (`saaras:v3` is the current default/recommended model), `mode`
    (`transcribe`). Response JSON has a `transcript` string field. Max 30s of audio
    per request (synchronous endpoint) - this app's own UtteranceDetector already
    caps one utterance at max_utterance_ms=15_000 (see app.voice.media_stream), so a
    single utterance always fits without needing the separate Batch API. WAV
    (16-bit PCM) is the documented preferred format, hence _make_wav_header above;
    8kHz and 16kHz are both supported per Sarvam's docs, so this does not depend on
    which of the two platform_speech_settings.sample_rate_hz allows.

    No `language_code` request field is documented for the `saaras:v3` model (unlike
    the older/legacy `saarika` models) - it auto-detects the spoken language instead
    and reports it back in the response's own `language_code` field, which is not
    otherwise used here: this app's reply language is decided separately by
    app.voice.service/app.agent, not by what language the caller's speech was in.
    """

    name = "sarvam"

    def __init__(self, http: ExternalHttp, api_key: str) -> None:
        self._http = http
        self._key = api_key

    async def transcribe(self, pcm16_audio: bytes, sample_rate_hz: int, language: str) -> SttResult:
        if not pcm16_audio:
            return SttResult("", None)
        wav_bytes = _make_wav_header(pcm16_audio, sample_rate_hz)
        try:
            response = await self._http.request(
                "POST",
                "https://api.sarvam.ai/speech-to-text",
                operation="stt_recognize",
                headers={"api-subscription-key": self._key},
                data={"model": "saaras:v3", "mode": "transcribe"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            )
        except UpstreamError:
            return SttResult("", None)
        if response.status_code != 200:
            # Same rationale as GoogleSpeechToText.transcribe's matching log line:
            # log the exact failure reason from the provider's own error body instead
            # of guessing again on the next bad call.
            logger.error(
                "sarvam STT non-200: status=%d body=%s",
                response.status_code,
                response.text[:1000],
            )
            return SttResult("", None)
        try:
            data = response.json()
        except ValueError:
            return SttResult("", None)
        transcript = str(data.get("transcript") or "").strip()
        # Sarvam's documented saaras:v3 response does not include a per-result
        # confidence score (unlike Google's `alternatives[0].confidence` above) -
        # VERIFY if Sarvam adds one later; None is passed through unchanged exactly
        # like Google's own "no confidence present" case already handles.
        return SttResult(transcript, None)


class DisabledTextToSpeech:
    name = "sarvam"

    async def synthesize(self, text: str, sample_rate_hz: int, language: str) -> bytes:
        logger.error("sarvam TTS selected but not implemented; VERIFY in DECISIONS.md")
        return b""


class FakeSpeechToText:
    """Deterministic test double: returns whatever was queued, does no real decoding."""

    name = "fake"

    def __init__(self, scripted: list[SttResult] | None = None) -> None:
        self.scripted = scripted or []
        self.calls: list[bytes] = []

    async def transcribe(self, pcm16_audio: bytes, sample_rate_hz: int, language: str) -> SttResult:
        self.calls.append(pcm16_audio)
        if self.scripted:
            return self.scripted.pop(0)
        return SttResult("", None)


class FakeTextToSpeech:
    """Deterministic test double: returns a fixed-size silent PCM buffer per call,
    proportional to text length, so tests can assert something was "spoken" without
    needing a real TTS engine.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str, sample_rate_hz: int, language: str) -> bytes:
        self.calls.append(text)
        if not text.strip():
            return b""
        frames = max(len(text), 1) * 32
        return b"\x00\x00" * frames


def make_stt(provider: str, http: ExternalHttp, api_key: str) -> SpeechToText:
    if provider == "google":
        return GoogleSpeechToText(http, api_key)
    if provider == "sarvam":
        return SarvamSpeechToText(http, api_key)
    raise ValueError(f"unknown stt provider: {provider!r}")


def make_tts(provider: str, http: ExternalHttp, api_key: str) -> TextToSpeech:
    if provider == "google":
        return GoogleTextToSpeech(http, api_key)
    return DisabledTextToSpeech()
