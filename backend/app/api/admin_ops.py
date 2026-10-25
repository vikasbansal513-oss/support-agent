from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import ReasonIn, StatusOut
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.flags import is_globally_paused

router = APIRouter(prefix="/admin", tags=["admin-ops"])
Factory = async_sessionmaker[AsyncSession]


async def _set(
    factory: Factory, admin: Principal, enabled: bool, reason: str, action: str
) -> StatusOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        await session.execute(
            text(
                "INSERT INTO platform_flags (name, enabled, reason, updated_by, updated_at)"
                " VALUES ('global_pause', :e, :r, :u, now())"
                " ON CONFLICT (name) DO UPDATE SET enabled = :e, reason = :r, updated_by = :u,"
                " updated_at = now()"
            ),
            {"e": enabled, "r": reason, "u": admin.user_id},
        )
        await record(
            session,
            AuditEvent(
                action=action,
                entity_type="platform",
                entity_id="global_pause",
                new_value={"enabled": enabled},
                reason=reason,
                actor_user_id=admin.user_id,
                actor_role="admin",
            ),
        )
    return StatusOut(status="paused" if enabled else "running")


@router.post("/global-pause")
async def global_pause(
    body: ReasonIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StatusOut:
    return await _set(factory, admin, True, body.reason, "admin.global_paused")


@router.post("/global-resume")
async def global_resume(
    body: ReasonIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StatusOut:
    return await _set(factory, admin, False, body.reason, "admin.global_resumed")


@router.get("/global-pause")
async def global_state(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StatusOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        paused = await is_globally_paused(session)
    return StatusOut(status="paused" if paused else "running")
