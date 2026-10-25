from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings
from app.integrations.http import ExternalHttp, UpstreamError

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_COMPAT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class ProviderTimeout(Exception):
    pass


class ProviderError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class RawCompletion:
    text: str
    input_tokens: int
    output_tokens: int


class LlmProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, max_tokens: int) -> RawCompletion: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, http: ExternalHttp, api_key: str, model: str) -> None:
        self._http = http
        self._key = api_key
        self.model = model

    async def complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        try:
            response = await self._http.request(
                "POST",
                ANTHROPIC_URL,
                operation="messages",
                headers={
                    "x-api-key": self._key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
        except UpstreamError as exc:
            if exc.kind == "timeout":
                raise ProviderTimeout() from exc
            raise ProviderError(exc.kind) from exc
        if response.status_code in (401, 403):
            raise ProviderError("auth_error")
        if response.status_code != 200:
            raise ProviderError(f"http_{response.status_code}")
        try:
            body = response.json()
            blocks = body["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            usage = body.get("usage") or {}
            return RawCompletion(
                text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ProviderError("bad_body") from exc


class OpenAICompatProvider:
    def __init__(
        self,
        http: ExternalHttp,
        api_key: str,
        model: str,
        base_url: str,
        name: str = "openai_compatible",
        limit_field: str = "max_tokens",
    ) -> None:
        self._http = http
        self._key = api_key
        self._url = f"{base_url.strip().rstrip('/')}/chat/completions"
        self._limit_field = limit_field
        self.name = name
        self.model = model

    async def complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        try:
            response = await self._http.request(
                "POST",
                self._url,
                operation="chat_completions",
                headers={
                    "authorization": f"Bearer {self._key}",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    self._limit_field: max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
        except UpstreamError as exc:
            if exc.kind == "timeout":
                raise ProviderTimeout() from exc
            raise ProviderError(exc.kind) from exc
        if response.status_code in (401, 403):
            raise ProviderError("auth_error")
        if response.status_code != 200:
            raise ProviderError(f"http_{response.status_code}")
        try:
            body = response.json()
            text = body["choices"][0]["message"].get("content") or ""
            usage = body.get("usage") or {}
            return RawCompletion(
                str(text),
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ProviderError("bad_body") from exc


@dataclass
class FakeProvider:
    name: str = "fake"
    model: str = "fake-model"
    responses: list[str | Exception] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    tokens: tuple[int, int] = (100, 20)

    async def complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        self.calls.append((system, user))
        if not self.responses:
            raise ProviderError("no_fake_response")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return RawCompletion(item, *self.tokens)


def make_provider(
    http: ExternalHttp, provider: str, model: str, api_key: str, base_url: str
) -> LlmProvider:
    if provider == "anthropic":
        return AnthropicProvider(http, api_key, model)
    url = base_url.strip() or OPENAI_COMPAT_BASE_URLS[provider]
    return OpenAICompatProvider(
        http,
        api_key,
        model,
        url,
        provider,
        "max_completion_tokens" if provider == "openai" else "max_tokens",
    )


def build_provider(http: ExternalHttp) -> LlmProvider:
    settings = get_settings()
    return make_provider(
        http,
        settings.llm_provider,
        settings.llm_model,
        settings.llm_api_key,
        settings.llm_base_url,
    )
