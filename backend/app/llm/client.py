import json
import logging
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from app.llm.guard import INJECTION_GUARD
from app.llm.provider import LlmProvider, ProviderError, ProviderTimeout, RawCompletion

logger = logging.getLogger("app.llm")
PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
T = TypeVar("T", bound=BaseModel)
Factory = async_sessionmaker[AsyncSession]
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
# TRUNCATION FIX (2026-11-19, flagged unverified - not exercised against a live
# provider from here, no app execution per this change's own constraints): this
# tenant's own Groq request logs showed every observed 400 "json_validate_failed"
# response (OpenAICompatProvider.complete's json_mode branch in app.llm.provider)
# had output_tokens exactly 600 - the old MAX_TOKENS ceiling - while the successful
# 200 responses alongside them had fewer (396, 475). That pattern (failure always at
# exactly the cap, success always under it) is consistent with the model's JSON
# output being cut off mid-structure when it hits the token limit, which produces
# syntactically invalid JSON and a 400 from Groq's own json_object-mode validator -
# though this is inferred from a handful of log rows, not confirmed by reproducing
# it. Raising the ceiling gives the model more room to finish its JSON before being
# cut off. This also doubles as a (likely secondary, less certain) contributor to
# the 429 TPM rate-limit this tenant hit separately: every 400 of this kind already
# triggers an immediate same-call retry (OpenAICompatProvider.complete, existing
# behavior, unchanged here) that resends the same ~2500 input tokens a second time
# within the same minute - fewer 400s should mean fewer of these doubled-up calls
# competing for the same per-minute token budget. VERIFY: watch this tenant's
# llm_calls/Groq dashboard rows after deploy - purpose='plan'/'confirm' outcome
# counts for invalid_json (app side) and 400/json_validate_failed (Groq side) should
# both drop; if 400s at the new ceiling still appear, the ceiling needs raising
# further or the real cause isn't truncation.
MAX_TOKENS = 900


class LlmUnavailable(Exception):
    pass


class LlmAuthError(LlmUnavailable):
    pass


class LlmInvalid(Exception):
    pass


class CallRecorder(Protocol):
    async def __call__(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID | None,
        purpose: str,
        provider: str,
        model: str,
        prompt_version: str | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        outcome: str,
    ) -> None: ...


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def split_prompt(prompt: str) -> str:
    marker = "<history>\n{{history}}"
    index = prompt.rfind(marker)
    return prompt[:index].rstrip() if index != -1 else prompt.rstrip()


def extract_json(raw: str) -> object:
    cleaned = raw.strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        found = _JSON_OBJECT.search(cleaned)
        if found is None:
            raise
        return json.loads(found.group(0))


def estimate_cost_micro(
    input_tokens: int, output_tokens: int, input_price: Decimal | None, output_price: Decimal | None
) -> int | None:
    if input_price is None or output_price is None:
        return None
    return int(round(Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price))


class DbCallRecorder:
    def __init__(self, factory: Factory) -> None:
        self._factory = factory

    async def __call__(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID | None,
        purpose: str,
        provider: str,
        model: str,
        prompt_version: str | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        outcome: str,
    ) -> None:
        try:
            async with session_scope(self._factory, tenant_id=tenant_id) as session:
                price = (
                    await session.execute(
                        text(
                            "SELECT input_per_million, output_per_million FROM llm_model_prices"
                            " WHERE provider = :p AND model = :m"
                        ),
                        {"p": provider, "m": model},
                    )
                ).first()
                cost = estimate_cost_micro(
                    input_tokens,
                    output_tokens,
                    price.input_per_million if price else None,
                    price.output_per_million if price else None,
                )
                await session.execute(
                    text(
                        "INSERT INTO llm_calls (run_id, purpose, provider, model, prompt_version,"
                        " input_tokens, output_tokens, est_cost_micro, latency_ms, outcome)"
                        " VALUES (:r, :pu, :pr, :m, :pv, :i, :o, :c, :l, :out)"
                    ),
                    {
                        "r": run_id,
                        "pu": purpose,
                        "pr": provider,
                        "m": model,
                        "pv": prompt_version,
                        "i": input_tokens,
                        "o": output_tokens,
                        "c": cost,
                        "l": latency_ms,
                        "out": outcome,
                    },
                )
        except Exception as exc:
            logger.error("llm call record failed", extra={"error_class": type(exc).__name__})


class LlmClient:
    def __init__(self, provider: LlmProvider, recorder: CallRecorder | None = None) -> None:
        self._provider = provider
        self._recorder = recorder

    async def _record(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID | None,
        purpose: str,
        prompt_version: str,
        started: float,
        outcome: str,
        completion: RawCompletion | None,
    ) -> None:
        if self._recorder is None:
            return
        await self._recorder(
            tenant_id=tenant_id,
            run_id=run_id,
            purpose=purpose,
            provider=self._provider.name,
            model=self._provider.model,
            prompt_version=prompt_version,
            input_tokens=completion.input_tokens if completion else 0,
            output_tokens=completion.output_tokens if completion else 0,
            latency_ms=int((time.monotonic() - started) * 1000),
            outcome=outcome,
        )

    async def complete_json(
        self,
        *,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID | None,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        prompt_version: str,
    ) -> T:
        full_system = f"{INJECTION_GUARD}\n\n{system}"
        prompt = user
        for attempt in range(2):
            started = time.monotonic()
            try:
                completion = await self._provider.complete(full_system, prompt, MAX_TOKENS)
            except ProviderTimeout as exc:
                await self._record(
                    tenant_id, run_id, purpose, prompt_version, started, "timeout", None
                )
                raise LlmUnavailable("timeout") from exc
            except ProviderError as exc:
                await self._record(
                    tenant_id, run_id, purpose, prompt_version, started, "error", None
                )
                if exc.kind == "auth_error":
                    logger.error("llm auth failed", extra={"provider": self._provider.name})
                    raise LlmAuthError(exc.kind) from exc
                raise LlmUnavailable(exc.kind) from exc
            try:
                parsed = schema.model_validate(extract_json(completion.text))
            except (ValueError, ValidationError):
                await self._record(
                    tenant_id, run_id, purpose, prompt_version, started, "invalid_json", completion
                )
                if attempt == 0:
                    prompt = (
                        f"{user}\n\nYour previous answer was not valid JSON for the required schema. "
                        "Return only the JSON object."
                    )
                    continue
                raise LlmInvalid("schema validation failed") from None
            await self._record(
                tenant_id, run_id, purpose, prompt_version, started, "ok", completion
            )
            return parsed
        raise LlmInvalid("unreachable")
