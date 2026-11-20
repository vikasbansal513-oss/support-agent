import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import (
    actions,
    admin_alerts,
    admin_audit,
    admin_auth,
    admin_autonomy,
    admin_billing,
    admin_control_tower,
    admin_jobs,
    admin_llm,
    admin_llm_settings,
    admin_monitoring,
    admin_ops,
    admin_overview,
    admin_speech_settings,
    admin_tenants,
    agent,
    approvals,
    auth,
    conversations,
    dashboard,
    handoffs,
    integrations,
    perf,
    policies,
    privacy,
    settings,
)
from app.api import (
    voice as voice_api,
)
from app.core.config import get_settings
from app.core.db import check_rls_safety, get_session_factory, session_scope, with_connection_retry
from app.core.errors import AppError, install_error_handlers
from app.core.logging import configure_logging
from app.core.monitoring import init_monitoring
from app.core.security_headers import SecurityHeadersMiddleware
from app.webhooks import plivo as plivo_webhook
from app.webhooks import voice as voice_webhook
from app.webhooks import voice_stream as voice_stream_webhook
from app.webhooks import whatsapp

logger = logging.getLogger("app.main")
system_router = APIRouter()


@system_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@system_router.get("/ready")
async def ready(
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
) -> dict[str, str]:
    try:
        async with session_scope(factory) as session:
            await session.execute(text("SELECT 1"))
            problems = await check_rls_safety(session)
    except Exception as exc:
        raise AppError("E-SYS-002", detail=f"db unavailable: {type(exc).__name__}") from exc
    if problems:
        raise AppError("E-SYS-002", detail="; ".join(problems))
    return {"status": "ready"}


@asynccontextmanager
async def _lifespan(_application: FastAPI) -> AsyncIterator[None]:
    """ROOT-CAUSE FIX (2026-11-20): a real Edesy voice call landed on a brand-new Cloud
    Run instance ~2m17s after a deployment rollout had just terminated the previous one.
    The external `neon-keep-alive-evening` Cloud Scheduler job (every 4 minutes) could
    not cover that gap - its last successful ping had gone to the OLD instance (whose
    own connection to Neon closed when it shut down), the NEW instance's connection pool
    started completely empty, and the next scheduled ping wasn't due for another ~2
    minutes. Neon's compute suspended again in that gap, so the customer's call paid the
    full compute-resume latency live (app.voice.service._start()'s first query took
    ~6.7s) - the agent's greeting alone then took ~20s end to end and the caller hung up
    before the real reply was ever spoken (see DECISIONS.md 2026-11-20 voice-silence
    investigation for the full log trail).

    Pinging the DB once here, at process startup, closes exactly that gap: every new
    Cloud Run revision wakes Neon (or confirms it's already warm) the instant it starts,
    before it can receive any real traffic - independent of, and faster than, the
    external Scheduler job's own cycle. This does not replace the Scheduler job (which
    still needs to keep Neon warm *between* deployments, while an instance sits idle) -
    it only covers the specific moment a fresh instance comes up.

    Uses with_connection_retry (same helper app.jobs.queue already uses for this exact
    Neon-suspend/resume failure mode - see DECISIONS.md P41) since a brand-new instance's
    very first connection attempt is the single most likely place to race a mid-resume
    connection drop. Best-effort only: Cloud Run's own startup/liveness probes (GET
    /ready, which already does a real DB round trip) are the actual gate on whether this
    instance is allowed to receive traffic - a failure here must never block startup or
    be mistaken for that gate, so it is logged and swallowed, not raised.
    VERIFY: not yet confirmed against a real deployment + real Edesy call together: the
    next deployment's logs should show this ping succeed within the first second or two
    of "Application startup complete", and a call arriving soon after should no longer
    show the ~6-7s _start() gap this fix targets.
    """
    try:
        factory = get_session_factory()

        async def _ping() -> None:
            async with session_scope(factory) as session:
                await session.execute(text("SELECT 1"))

        await with_connection_retry(_ping)
    except Exception:
        logger.exception("startup db warm-up ping failed; continuing without it")
    yield


def create_app() -> FastAPI:
    configure_logging()
    init_monitoring("api")
    application = FastAPI(title="support-agent", lifespan=_lifespan)
    install_error_handlers(application)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    application.include_router(auth.router)
    application.include_router(admin_auth.router)
    application.include_router(admin_tenants.router)
    application.include_router(admin_autonomy.router)
    application.include_router(integrations.router)
    application.include_router(policies.router)
    application.include_router(settings.router)
    application.include_router(agent.router)
    application.include_router(admin_jobs.router)
    application.include_router(admin_llm.router)
    application.include_router(admin_llm_settings.router)
    application.include_router(admin_speech_settings.router)
    application.include_router(admin_billing.router)
    application.include_router(conversations.router)
    application.include_router(handoffs.router)
    application.include_router(actions.router)
    application.include_router(approvals.router)
    application.include_router(voice_api.router)
    application.include_router(dashboard.router)
    application.include_router(privacy.router)
    application.include_router(admin_ops.router)
    application.include_router(admin_overview.router)
    application.include_router(admin_audit.router)
    application.include_router(admin_alerts.router)
    application.include_router(admin_control_tower.router)
    application.include_router(admin_monitoring.router)
    application.include_router(perf.router)
    application.include_router(whatsapp.router)
    application.include_router(voice_webhook.router)
    application.include_router(voice_stream_webhook.router)
    application.include_router(plivo_webhook.router)
    application.include_router(system_router)
    return application


app = create_app()
