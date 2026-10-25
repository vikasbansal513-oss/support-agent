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
    ) -> None:
        self.provider = provider
        self._recorder = recorder
        self._transport = transport
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._sleep = sleep

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
                        method, url, headers=headers, params=params, json=json, data=data
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
                            await self._sleep(self._delay(response, attempt))
                            continue
                        raise last_error
                    return response
                last_error = UpstreamError(error_class or "network_error")
                if attempt < self._max_attempts:
                    await self._sleep(self._delay(None, attempt))
        raise last_error or UpstreamError("network_error")

    @staticmethod
    def _delay(response: httpx.Response | None, attempt: int) -> float:
        if response is not None and response.status_code == 429:
            raw = response.headers.get("retry-after", "")
            if raw.isdigit():
                return float(min(float(raw), MAX_RETRY_AFTER))
        return float(min(0.5 * (2 ** (attempt - 1)), MAX_RETRY_AFTER))

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
