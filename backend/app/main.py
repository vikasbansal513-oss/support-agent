from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import (
    actions,
    admin_auth,
    admin_autonomy,
    admin_billing,
    admin_jobs,
    admin_llm,
    admin_llm_settings,
    admin_ops,
    admin_tenants,
    agent,
    approvals,
    auth,
    conversations,
    dashboard,
    handoffs,
    integrations,
    policies,
    privacy,
    settings,
)
from app.api import (
    voice as voice_api,
)
from app.core.config import get_settings
from app.core.db import check_rls_safety, get_session_factory, session_scope
from app.core.errors import AppError, install_error_handlers
from app.core.logging import configure_logging
from app.core.monitoring import init_monitoring
from app.webhooks import voice as voice_webhook
from app.webhooks import whatsapp

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


def create_app() -> FastAPI:
    configure_logging()
    init_monitoring("api")
    application = FastAPI(title="support-agent")
    install_error_handlers(application)
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
    application.include_router(admin_billing.router)
    application.include_router(conversations.router)
    application.include_router(handoffs.router)
    application.include_router(actions.router)
    application.include_router(approvals.router)
    application.include_router(voice_api.router)
    application.include_router(dashboard.router)
    application.include_router(privacy.router)
    application.include_router(admin_ops.router)
    application.include_router(whatsapp.router)
    application.include_router(voice_webhook.router)
    application.include_router(system_router)
    return application


app = create_app()
