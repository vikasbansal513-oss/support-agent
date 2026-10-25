from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_owner, require_user
from app.auth.service import Principal
from app.contracts.contracts import AgentStatusOut
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope

router = APIRouter(prefix="/agent", tags=["agent"])
Factory = async_sessionmaker[AsyncSession]


async def _status(session: AsyncSession) -> AgentStatusOut:
    tenant = (
        await session.execute(
            text("SELECT mode, agent_paused, status FROM tenants WHERE id = app_current_tenant()")
        )
    ).one()
    version = (
        await session.execute(text("SELECT version FROM policies WHERE status = 'published'"))
    ).scalar_one_or_none()
    errors = (
        (
            await session.execute(
                text("SELECT kind FROM integrations WHERE status = 'auth_error' ORDER BY kind")
            )
        )
        .scalars()
        .all()
    )
    return AgentStatusOut(
        mode=tenant.mode,
        agent_paused=tenant.agent_paused,
        tenant_status=tenant.status,
        published_policy_version=version,
        integration_errors=list(errors),
    )


@router.get("/status")
async def agent_status(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AgentStatusOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await _status(s)


async def _set_paused(factory: Factory, principal: Principal, paused: bool) -> AgentStatusOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        await s.execute(
            text("UPDATE tenants SET agent_paused = :p WHERE id = app_current_tenant()"),
            {"p": paused},
        )
        await record(
            s,
            AuditEvent(
                action="agent.paused" if paused else "agent.resumed",
                entity_type="tenant",
                actor_user_id=principal.user_id,
                actor_role=principal.role,
            ),
        )
        return await _status(s)


@router.post("/pause")
async def pause(
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AgentStatusOut:
    return await _set_paused(factory, principal, True)


@router.post("/resume")
async def resume(
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AgentStatusOut:
    return await _set_paused(factory, principal, False)
