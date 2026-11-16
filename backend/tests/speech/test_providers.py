import base64
import json
from collections.abc import Callable

import httpx
import pytest

from app.integrations.http import ExternalHttp
from app.speech.providers import (
    DisabledSpeechToText,
    DisabledTextToSpeech,
    FakeSpeechToText,
    FakeTextToSpeech,
    GoogleSpeechToText,
    GoogleTextToSpeech,
    SttResult,
    _strip_wav_header,
    make_stt,
    make_tts,
)

Handler = Callable[[httpx.Request], httpx.Response]


async def _nosleep(_: float) -> None:
    return None


def make_http(handler: Handler) -> ExternalHttp:
    return ExternalHttp("test", transport=httpx.MockTransport(handler), sleep=_nosleep)


def _wav(pcm: bytes) -> bytes:
    """Minimal canonical 16-bit mono PCM WAV wrapper, just enough for
    _strip_wav_header to find the "data" chunk - mirrors what Google's
    text:synthesize LINEAR16 response actually returns (a WAV file, not raw PCM).
    """
    return (
        b"RIFF"
        + (36 + len(pcm)).to_bytes(4, "little")
        + b"WAVEfmt "
        + b"\x00" * 16
        + b"data"
        + len(pcm).to_bytes(4, "little")
        + pcm
    )


async def test_google_stt_parses_transcript_and_confidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "k-123"
        body = json.loads(request.content)
        assert body["config"]["sampleRateHertz"] == 16000
        assert body["config"]["encoding"] == "LINEAR16"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"alternatives": [{"transcript": "order kahan hai", "confidence": 0.91}]}
                ]
            },
        )

    stt = GoogleSpeechToText(make_http(handler), "k-123")
    result = await stt.transcribe(b"\x00\x01" * 100, 16000, "hi-IN")
    assert result.text == "order kahan hai"
    assert result.confidence == pytest.approx(0.91)


async def test_google_stt_no_results_is_empty_not_an_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    stt = GoogleSpeechToText(make_http(handler), "k-123")
    result = await stt.transcribe(b"\x00\x01" * 100, 16000, "hi-IN")
    assert result.text == "" and result.confidence is None


async def test_google_stt_empty_audio_skips_the_call() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    stt = GoogleSpeechToText(make_http(handler), "k-123")
    result = await stt.transcribe(b"", 16000, "hi-IN")
    assert result.text == "" and not called


async def test_google_stt_http_error_is_empty_not_raised() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "bad key"})

    stt = GoogleSpeechToText(make_http(handler), "bad-key")
    result = await stt.transcribe(b"\x00\x01" * 100, 16000, "hi-IN")
    assert result.text == "" and result.confidence is None


async def test_google_tts_strips_wav_header_to_raw_pcm() -> None:
    pcm = b"\x01\x02\x03\x04" * 50
    wav = _wav(pcm)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["audioConfig"]["sampleRateHertz"] == 16000
        assert body["voice"]["languageCode"] == "en-IN"
        return httpx.Response(200, json={"audioContent": base64.b64encode(wav).decode()})

    tts = GoogleTextToSpeech(make_http(handler), "k-123")
    out = await tts.synthesize("your order is on the way", 16000, "en-IN")
    assert out == pcm


async def test_google_tts_empty_text_skips_the_call() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"audioContent": ""})

    tts = GoogleTextToSpeech(make_http(handler), "k-123")
    out = await tts.synthesize("   ", 16000, "en-IN")
    assert out == b"" and not called


async def test_google_tts_http_error_is_empty_not_raised() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "upstream down"})

    tts = GoogleTextToSpeech(make_http(handler), "k-123")
    out = await tts.synthesize("hello", 16000, "en-IN")
    assert out == b""


def test_strip_wav_header_without_data_chunk_returns_input_unchanged() -> None:
    assert _strip_wav_header(b"not a wav file") == b"not a wav file"


async def test_fake_providers_are_deterministic_for_tests() -> None:
    stt = FakeSpeechToText([SttResult("hi", 0.5)])
    result = await stt.transcribe(b"abc", 16000, "hi-IN")
    assert result.text == "hi" and stt.calls == [b"abc"]

    tts = FakeTextToSpeech()
    out = await tts.synthesize("hello there", 16000, "en-IN")
    assert out and tts.calls == ["hello there"]
    assert await tts.synthesize("", 16000, "en-IN") == b""


async def test_sarvam_choice_is_an_honest_disabled_stub_not_invented() -> None:
    http = make_http(lambda _r: httpx.Response(500))
    stt = make_stt("sarvam", http, "k")
    tts = make_tts("sarvam", http, "k")
    assert isinstance(stt, DisabledSpeechToText)
    assert isinstance(tts, DisabledTextToSpeech)
    assert (await stt.transcribe(b"x", 16000, "hi-IN")).text == ""
    assert await tts.synthesize("x", 16000, "hi-IN") == b""


def test_make_stt_tts_select_google_by_name() -> None:
    http = make_http(lambda _r: httpx.Response(200))
    assert isinstance(make_stt("google", http, "k"), GoogleSpeechToText)
    assert isinstance(make_tts("google", http, "k"), GoogleTextToSpeech)
