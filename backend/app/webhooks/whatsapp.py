import hashlib
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels import normalize
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError, new_error_id
from app.jobs import handlers, queue
from app.webhooks.security import check_ip_allowlist, request_ip, resolve_token
from app.worker import drain_queue

logger = logging.getLogger("app.webhooks.whatsapp")
router = APIRouter(tags=["webhooks"])
Factory = async_sessionmaker[AsyncSession]
MAX_BODY_BYTES = 262_144
INLINE_WORKER = "inline:whatsapp_webhook"
INSTANT_DRAIN_ENABLED = get_settings().instant_drain_enabled

_INSERT_EVENT = text(
    "INSERT INTO inbound_events (channel, external_id, raw_payload, status, skip_reason)"
    " VALUES (:ch, :ext, CAST(:raw AS jsonb), :st, :reason)"
    " ON CONFLICT (tenant_id, channel, external_id) DO NOTHING RETURNING id"
)


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:40]


async def _drain_now(factory: Factory, job_id: uuid.UUID) -> None:
    """Best-effort: process the just-enqueued job inline, on this request, instead of
    waiting for the next worker tick. Cheap (request-based) billing, instant reply. If it
    races with a worker tick or fails, the normal job-queue retry/worker path still covers
    it - draining never replaces the queue, only speeds it up when it works."""
    try:
        if not handlers.inbound_runner_installed():
            # API service (unlike worker.py's main()) never installs the real agent
            # pipeline by default; a test may have installed its own fake runner instead,
            # so only fill this in when nothing is installed yet - never override one.
            from app.agent import runner

            runner.install()
        await drain_queue(factory, job_id, INLINE_WORKER)
    except Exception:
        logger.exception("inline drain failed, leaving job for the worker")


async def _store(
    factory: Factory,
    tenant_id: uuid.UUID,
    external_id: str,
    raw: dict[str, Any],
    status: str,
    reason: str | None,
    enqueue_job: bool,
) -> str:
    job_id: uuid.UUID | None = None
    async with session_scope(factory, tenant_id=tenant_id) as session:
        row = (
            await session.execute(
                _INSERT_EVENT,
                {
                    "ch": "whatsapp",
                    "ext": external_id,
                    "raw": json.dumps(raw, default=str),
                    "st": status,
                    "reason": reason,
                },
            )
        ).first()
        if row is None:
            return "duplicate"
        if enqueue_job:
            job_id = await queue.enqueue(
                session,
                tenant_id,
                "process_inbound",
                {"inbound_event_id": str(row.id)},
                idempotency_key=f"inb:{row.id}",
            )
        else:
            return "skipped"
    # outer transaction has committed by here, so the job row is now visible to the
    # separate session claim_by_id() opens - draining earlier (inside the block above)
    # would try to claim a row no other connection can see yet.
    if job_id is not None and INSTANT_DRAIN_ENABLED:
        await _drain_now(factory, job_id)
    return "queued"


@router.post("/webhooks/whatsapp/{token}")
async def whatsapp_webhook(
    token: str,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> JSONResponse:
    try:
        tenant = await resolve_token(factory, token)
        if tenant is None:
            raise AppError("E-WEB-001", status_code=404)
        check_ip_allowlist(request_ip(request))
        raw_bytes = await request.body()
        if len(raw_bytes) > MAX_BODY_BYTES:
            return JSONResponse({"status": "skipped"})
        try:
            body = json.loads(raw_bytes)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            outcome = await _store(
                factory,
                tenant.tenant_id,
                f"skip:{_digest(raw_bytes)}",
                {"reason": "not_json_object"},
                "skipped",
                "UNPARSEABLE",
                False,
            )
            return JSONResponse({"status": outcome})
        app_name = str(body.get("app") or "")
        if tenant.app_name and app_name and app_name != tenant.app_name:
            outcome = await _store(
                factory,
                tenant.tenant_id,
                f"skip:{_digest(raw_bytes)}",
                {
                    "type": str(body.get("type")),
                    "app": app_name,
                    "expected_app": tenant.app_name,
                    "raw": body,
                },
                "skipped",
                "APP_MISMATCH",
                False,
            )
            return JSONResponse({"status": outcome})
        inbound = normalize.from_gupshup(body)
        if inbound is None:
            outcome = await _store(
                factory,
                tenant.tenant_id,
                f"skip:{_digest(raw_bytes)}",
                {"type": str(body.get("type")), "raw": body},
                "skipped",
                "NOT_A_CUSTOMER_MESSAGE",
                False,
            )
            return JSONResponse({"status": outcome})
        outcome = await _store(
            factory,
            tenant.tenant_id,
            inbound.external_id,
            {"normalized": inbound.model_dump(mode="json"), "raw": body},
            "queued",
            None,
            True,
        )
        return JSONResponse({"status": outcome})
    except AppError:
        raise
    except Exception as exc:
        error_id = new_error_id()
        logger.error(
            "webhook failed",
            extra={"error_id": error_id, "error_class": type(exc).__name__},
        )
        return JSONResponse({"error": "internal", "error_id": error_id}, status_code=500)
