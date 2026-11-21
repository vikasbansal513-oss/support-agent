import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from app.core.masking import redact_text

logger = logging.getLogger("app.integrations.http")

RETRY_STATUS = frozenset({500, 502, 503, 504})
MAX_RETRY_AFTER = 30.0


@dataclass(frozen=True)
class CallRecord:
    provider: str
    operation: str
    request_summary: dict[str, Any]
    http_status: int | None
    response_body_redacted: str | None
    error_class: str | None
    latency_ms: int


Recorder = Callable[[CallRecord], Awaitable[None]]


class DbCallRecorder:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        tenant_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
    ) -> None:
        self._factory = factory
        self._tenant_id = tenant_id
        self._run_id = run_id

    async def __call__(self, record: CallRecord) -> None:
        try:
            async with session_scope(self._factory, tenant_id=self._tenant_id) as session:
                await session.execute(
                    text(
                        "INSERT INTO external_calls (run_id, provider, operation, request_summary,"
                        " http_status, response_body_redacted, error_class, latency_ms) VALUES"
                        " (:run, :provider, :op, CAST(:summary AS jsonb), :status, :body,"
                        " :err, :ms)"
                    ),
                    {
                        "run": self._run_id,
                        "provider": record.provider,
                        "op": record.operation,
                        "summary": _json(record.request_summary),
                        "status": record.http_status,
                        "body": record.response_body_redacted,
                        "err": record.error_class,
                        "ms": record.latency_ms,
                    },
                )
        except Exception as exc:
            logger.error("external call record failed", extra={"error_class": type(exc).__name__})


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


class UpstreamError(Exception):
    def __init__(self, kind: str, status: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.status = status


class ExternalHttp:
    def __init__(
        self,
        provider: str,
        *,
        recorder: Recorder | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        # ROOT-CAUSE FIX (2026-11-20): a live voice call hit Groq's 429 (tokens-
        # per-minute) limit and got zero reply - app.agent.runner's LIVE_CALL_LLM_
        # MAX_ATTEMPTS=1 means a live call never retries at all today, by design
        # (the default 3-attempt/up-to-30s-backoff policy below was itself a past
        # root cause of ~100s of dead air on a call - see that constant's own
        # comment). Groq's 429 body told us the real wait needed was only 637.5ms
        # ("Please try again in 637.5ms") - far short of the 30s cap this class
        # otherwise allows. This optional override lets a caller (just the live
        # voice path, via LIVE_CALL_LLM_MAX_RETRY_DELAY_S in app.agent.runner) keep
        # the existing exponential-backoff/Retry-After logic in _delay() below but
        # cap it to a small ceiling, so a single short, cheap retry stays possible
        # without reopening the old 100s-silence failure mode. None (the default)
        # keeps every existing caller's behavior (anthropic/openai/groq non-voice
        # calls, Sarvam, Google TTS, etc.) byte-for-byte unchanged.
        max_retry_delay_s: float | None = None,
    ) -> None:
        self.provider = provider
        self._recorder = recorder
        self._transport = transport
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._max_retry_delay = (
            max_retry_delay_s if max_retry_delay_s is not None else MAX_RETRY_AFTER
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: Any = None,
        data: dict[str, str] | None = None,
        # 2026-11-18: added for app.speech.providers.SarvamSpeechToText - Sarvam's
        # /speech-to-text endpoint takes the audio as a multipart file part (per its
        # REST API docs: Content-Type multipart/form-data, `file` field), not JSON -
        # none of the existing callers (Google STT/TTS, both JSON-body) pass this, so
        # it defaults to None and changes nothing for them.
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        parts = urlsplit(url)
        summary = {"method": method, "host": parts.netloc, "path": parts.path}
        last_error: UpstreamError | None = None
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            for attempt in range(1, self._max_attempts + 1):
                started = time.monotonic()
                response: httpx.Response | None = None
                error_class: str | None = None
                try:
                    response = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json,
                        data=data,
                        files=files,
                    )
                except httpx.TimeoutException:
                    error_class = "timeout"
                except httpx.TransportError as exc:
                    error_class = type(exc).__name__
                latency = int((time.monotonic() - started) * 1000)
                status = response.status_code if response is not None else None
                await self._record(operation, summary, response, error_class, latency)
                if response is not None and status is not None:
                    if status == 429 or status in RETRY_STATUS:
                        last_error = UpstreamError(
                            "rate_limited" if status == 429 else "server_error", status
                        )
                        if attempt < self._max_attempts:
                            # TEMPORARY DIAGNOSTIC (2026-11-17): this retry path (and the
                            # timeout/network branch below) previously had no logger call at
                            # all - only the silent DB-recorded CallRecord above - so a slow
                            # upstream (e.g. a request that exceeds `timeout` and gets
                            # retried) produced zero visible log output for the whole time it
                            # was stuck, which is indistinguishable from the app doing nothing.
                            # This is the leading suspect for an observed ~45-60s silent gap
                            # on real voice calls between STT finishing and the LLM reply.
                            # Remove once confirmed/fixed.
                            delay = self._delay(response, attempt)
                            logger.warning(
                                "external http retry: provider=%s operation=%s attempt=%d/%d "
                                "status=%d latency_ms=%d next_delay_s=%.1f",
                                self.provider,
                                operation,
                                attempt,
                                self._max_attempts,
                                status,
                                latency,
                                delay,
                            )
                            await self._sleep(delay)
                            continue
                        raise last_error
                    return response
                last_error = UpstreamError(error_class or "network_error")
                if attempt < self._max_attempts:
                    delay = self._delay(None, attempt)
                    logger.warning(
                        "external http retry: provider=%s operation=%s attempt=%d/%d "
                        "error_class=%s latency_ms=%d next_delay_s=%.1f",
                        self.provider,
                        operation,
                        attempt,
                        self._max_attempts,
                        error_class or "network_error",
                        latency,
                        delay,
                    )
                    await self._sleep(delay)
        raise last_error or UpstreamError("network_error")

    def _delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None and response.status_code == 429:
            raw = response.headers.get("retry-after", "")
            # ROOT-CAUSE FIX (2026-11-20): was raw.isdigit(), which only matches a
            # whole-number-of-seconds header (e.g. "1") - Groq's actual 429 for this
            # tenant asked for a sub-second wait ("Please try again in 637.5ms"), and
            # a provider that echoes that precision in the header (e.g. "0.6375")
            # would fail isdigit() and fall through to the slower exponential-backoff
            # branch below instead of the short wait the provider itself asked for.
            # float() accepts both "1" and "0.6375"; a header that isn't a number at
            # all (missing, or some non-numeric value) still falls through safely.
            if raw:
                try:
                    return float(min(float(raw), self._max_retry_delay))
                except ValueError:
                    pass
        return float(min(0.5 * (2 ** (attempt - 1)), self._max_retry_delay))

    async def _record(
        self,
        operation: str,
        summary: dict[str, Any],
        response: httpx.Response | None,
        error_class: str | None,
        latency_ms: int,
    ) -> None:
        if self._recorder is None:
            return
        status = response.status_code if response is not None else None
        body: str | None = None
        if response is not None and status is not None and status >= 400:
            body = redact_text(response.text)[:2048]
        if status is not None and status >= 400 and error_class is None:
            error_class = f"http_{status}"
        await self._recorder(
            CallRecord(self.provider, operation, summary, status, body, error_class, latency_ms)
        )
