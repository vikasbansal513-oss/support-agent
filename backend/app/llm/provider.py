from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings
from app.integrations.http import ExternalHttp, UpstreamError

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


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


def build_provider(http: ExternalHttp) -> LlmProvider:
    settings = get_settings()
    return AnthropicProvider(http, settings.llm_api_key, settings.llm_model)
