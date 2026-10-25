import json

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.integrations.http import ExternalHttp
from app.llm.provider import (
    AnthropicProvider,
    OpenAICompatProvider,
    ProviderError,
    build_provider,
)


def provider_for(
    handler: httpx.MockTransport, limit_field: str = "max_tokens"
) -> OpenAICompatProvider:
    http = ExternalHttp("groq", transport=handler, max_attempts=1)
    return OpenAICompatProvider(
        http,
        "key",
        "llama-3.3-70b-versatile",
        "https://api.groq.com/openai/v1/",
        "groq",
        limit_field,
    )


async def test_request_and_response_shape() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"a": 1}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5},
            },
        )

    result = await provider_for(httpx.MockTransport(handler)).complete("sys", "usr", 600)
    assert (result.text, result.input_tokens, result.output_tokens) == ('{"a": 1}', 11, 5)
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["max_tokens"] == 600
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


async def test_max_completion_tokens_param() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    await provider_for(httpx.MockTransport(handler), "max_completion_tokens").complete("s", "u", 50)
    assert seen["max_completion_tokens"] == 50
    assert "max_tokens" not in seen


@pytest.mark.parametrize(
    ("status", "kind"), [(401, "auth_error"), (403, "auth_error"), (400, "http_400")]
)
async def test_error_status(status: int, kind: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={}))
    with pytest.raises(ProviderError) as info:
        await provider_for(transport).complete("s", "u", 10)
    assert info.value.kind == kind


async def test_bad_body() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(ProviderError) as info:
        await provider_for(transport).complete("s", "u", 10)
    assert info.value.kind == "bad_body"


def test_build_provider_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    http = ExternalHttp("x")

    def build(**env: str) -> object:
        for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        try:
            return build_provider(http)
        finally:
            get_settings.cache_clear()

    assert isinstance(build(LLM_API_KEY="k"), AnthropicProvider)
    groq = build(LLM_PROVIDER="groq", LLM_MODEL="llama-3.3-70b-versatile", LLM_API_KEY="k")
    assert isinstance(groq, OpenAICompatProvider)
    assert groq.name == "groq"
    custom = build(
        LLM_PROVIDER="openai_compatible",
        LLM_MODEL="m",
        LLM_BASE_URL="https://example.com/v1",
        LLM_API_KEY="k",
    )
    assert isinstance(custom, OpenAICompatProvider)


def test_settings_validation() -> None:
    with pytest.raises(ValueError):
        Settings(llm_provider="groq")
    with pytest.raises(ValueError):
        Settings(llm_provider="openai_compatible", llm_model="m")
    assert Settings(llm_provider="groq", llm_model="llama-3.3-70b-versatile").llm_provider == "groq"
