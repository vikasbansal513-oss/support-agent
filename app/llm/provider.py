import asyncio
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.integrations.http import ExternalHttp, UpstreamError

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_COMPAT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
# RATE-LIMIT RETRY (2026-11-19): see OpenAICompatProvider.complete's 429 branch. Capped
# regardless of what the provider's Retry-After header or error message says, so this
# retry can never itself eat a large chunk of voice's 25s TURN_TIMEOUT_S
# (app.voice.service._finish_turn) even if a provider ever reports a multi-second wait.
MAX_429_RETRY_DELAY_S = 2.0
_RETRY_MS_RE = re.compile(r"try again in\s*(\d+(?:\.\d+)?)\s*(ms|s)\b", re.IGNORECASE)


def _retry_after_seconds(response: httpx.Response, *, default: float, cap: float) -> float:
    """Best-effort wait before retrying a 429: prefer the standard Retry-After header,
    else parse Groq/OpenAI's own "...Please try again in 690ms" error message, else use
    `default`. Always capped at `cap`."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(max(float(header), 0.0), cap)
        except ValueError:
            pass
    try:
        body = response.json()
        message = str((body.get("error") or {}).get("message") or "")
    except Exception:
        message = ""
    match = _RETRY_MS_RE.search(message)
    if match:
        value = float(match.group(1))
        seconds = value / 1000.0 if match.group(2).lower() == "ms" else value
        return min(max(seconds, 0.0), cap)
    return min(default, cap)


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
        if not self._key:
            raise ProviderError("auth_error")
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
        json_mode: bool = False,
    ) -> None:
        self._http = http
        self._key = api_key
        self._url = f"{base_url.strip().rstrip('/')}/chat/completions"
        self._limit_field = limit_field
        self._json_mode = json_mode
        self.name = name
        self.model = model

    def _payload(
        self, system: str, user: str, max_tokens: int, *, json_mode: bool
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            self._limit_field: max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            # See make_provider's comment: this method's one caller
            # (app.llm.client.complete_json) always expects a JSON object back
            # already, so there is no non-JSON use of this provider to protect.
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def _post(self, payload: dict[str, object]) -> httpx.Response:
        try:
            return await self._http.request(
                "POST",
                self._url,
                operation="chat_completions",
                headers={
                    "authorization": f"Bearer {self._key}",
                    "content-type": "application/json",
                },
                json=payload,
            )
        except UpstreamError as exc:
            if exc.kind == "timeout":
                raise ProviderTimeout() from exc
            raise ProviderError(exc.kind) from exc

    async def complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        if not self._key:
            raise ProviderError("auth_error")
        payload = self._payload(system, user, max_tokens, json_mode=self._json_mode)
        response = await self._post(payload)
        if response.status_code == 429:
            # RATE-LIMIT RETRY (2026-11-19): a real voice call went completely silent
            # because a transient per-minute token-rate-limit (429) was treated as an
            # immediate hard failure, with zero retry - app.llm.client.complete_json
            # just turns any ProviderError straight into LlmUnavailable, so the agent
            # had no reply to give and the caller sat in silence until they hung up
            # (confirmed via this tenant's own Groq dashboard logs: TPM limit 8000,
            # 5295 already used, 2797 requested - over by only 92 tokens, with Groq's
            # own error body saying "Please try again in 690ms", i.e. the per-minute
            # window was seconds from clearing on its own). One short, capped retry
            # (_retry_after_seconds, MAX_429_RETRY_DELAY_S above) turns that into a
            # normal reply instead of silence. Exactly one retry, not a loop - a
            # second consecutive 429 falls through to the unchanged status check below
            # and raises exactly as before, so a genuinely sustained rate-limit outage
            # still fails fast rather than turning into a long hang.
            delay = _retry_after_seconds(response, default=1.0, cap=MAX_429_RETRY_DELAY_S)
            await asyncio.sleep(delay)
            response = await self._post(payload)
        if self._json_mode and response.status_code == 400:
            # LATENCY FIX (2026-11-19) follow-up: json_mode is on for every
            # OpenAI-compatible provider now, including a tenant's own custom
            # "openai_compatible" base_url and whatever model they pick on the
            # dashboard (app/api/admin_llm_settings.py) - not every model a tenant
            # could choose is guaranteed to accept response_format: json_object (some
            # reject an unrecognized field with a hard 400 instead of just ignoring
            # it, unlike the groq/openai models this was verified against - see
            # make_provider's comment). A 400 is the one case worth a one-time,
            # same-call retry without response_format, since otherwise a tenant
            # switching providers could turn this latency optimization into a total
            # outage for that provider. Any other status (200, 401/403, 5xx, etc.)
            # falls through unchanged - this never retries a plain bad-request from
            # the model itself (e.g. a tenant with an oversized max_tokens setting
            # would still get a 400 even without response_format, and the retry below
            # would just reproduce the identical failure).
            response = await self._post(self._payload(system, user, max_tokens, json_mode=False))
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
        # LATENCY FIX (2026-11-19): a real tenant's llm_calls history showed 5 of the
        # last 10 "plan" purpose calls (the only caller of LlmProvider.complete - see
        # app.llm.client.complete_json, which is this method's one call site) coming
        # back as outcome="invalid_json" - complete_json's own retry loop
        # (for attempt in range(2)) then re-runs the *entire* LLM call a second time,
        # roughly doubling that turn's latency (observed ~8s of a live voice call's
        # ~12.5s "v1 run" step was this single LLM round trip, on a turn that hit this
        # retry). Forcing the provider's native JSON mode makes the model's own output
        # valid JSON far more often, instead of relying only on the prompt's text
        # instructions - this should cut most of those invalid_json retries, not just
        # reduce how long each individual call takes. Originally restricted to "groq"
        # and "openai" only (the two confirmed by this tenant's own llm_calls rows at
        # the time: provider=groq, model in {llama-3.3-70b-versatile,
        # openai/gpt-oss-20b}) - widened (2026-11-19, same day) to every
        # OpenAI-compatible provider ("openrouter" and a tenant's own custom
        # "openai_compatible" base_url included), per this tenant's own requirement
        # that the fix not be tied to one provider, since tenants pick their provider
        # and model freely on the dashboard (app/api/admin_llm_settings.py). The risk
        # this raises - a model that rejects an unrecognized response_format field
        # outright (a hard 400) rather than merely returning imperfect JSON - is
        # handled in OpenAICompatProvider.complete itself: a 400 while json_mode is on
        # triggers one same-call retry without response_format before this is treated
        # as a real failure, so a provider/model that does not support this mode falls
        # back to today's exact behavior (prompt-only JSON instructions) automatically,
        # at the cost of one extra round trip only on that first such call per model
        # (every call after that still pays it again - this provider has no per-model
        # memory of a prior 400 - but never anything worse than before this change).
        # complete_json's two callers (plan_node's "plan" prompt and
        # classify_confirmation_llm's "confirm" prompt - see app/prompts/plan.v1.md and
        # app/prompts/confirm.v1.md) already instruct the model to answer in JSON and
        # already say "json" in the prompt text, which is this mode's own requirement
        # to be accepted on OpenAI-compatible APIs generally - no prompt change needed.
        # VERIFY: not exercised against any live provider from here (no app
        # execution/pytest per this change's own constraints) - only checked with
        # py_compile/ruff/mypy and against the two prompt files above, and the 400-retry
        # path has no automated test covering it yet. Confirm on the next few real
        # calls' llm_calls rows that invalid_json outcomes for purpose='plan' drop (the
        # SQL used to find this: SELECT purpose, outcome, latency_ms, created_at FROM
        # llm_calls WHERE purpose = 'plan' ORDER BY created_at DESC LIMIT 10) - and if a
        # tenant is on a provider/model that doesn't support this mode, watch for its
        # latency instead going up slightly (one extra round trip every call) rather
        # than down.
        json_mode=True,
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
