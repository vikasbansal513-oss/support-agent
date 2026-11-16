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


class DisabledSpeechToText:
    """Sarvam STT is chosen in platform settings but not yet built - same
    interface+disabled-stub pattern as app.integrations.shopify_write's
    DisabledShopifyWriter, so the admin choice is honest (no invented API shape) and
    nothing silently no-ops as success.
    """

    name = "sarvam"

    async def transcribe(self, pcm16_audio: bytes, sample_rate_hz: int, language: str) -> SttResult:
        logger.error("sarvam STT selected but not implemented; VERIFY in DECISIONS.md")
        return SttResult("", None)


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
    return DisabledSpeechToText()


def make_tts(provider: str, http: ExternalHttp, api_key: str) -> TextToSpeech:
    if provider == "google":
        return GoogleTextToSpeech(http, api_key)
    return DisabledTextToSpeech()
