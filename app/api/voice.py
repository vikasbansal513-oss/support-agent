import uuid
from typing import cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_user
from app.auth.service import Principal
from app.core.db import get_session_factory, session_scope
from app.voice import admin
from app.voice.contracts import (
    VoiceCallDetail,
    VoiceCallOut,
    VoiceFeedbackIn,
    VoiceSettingsOut,
    VoiceStatsOut,
)

router = APIRouter(prefix="/voice", tags=["voice"])
Factory = async_sessionmaker[AsyncSession]


@router.get("/settings")
async def get_voice_settings(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> VoiceSettingsOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        # require_user guarantees a brand-realm principal, which always has a tenant_id
        return await admin.get_settings_out(s, cast(uuid.UUID, principal.tenant_id))


# Calling number/provider setup is admin-only now (platform admin dashboard,
# PUT /admin/tenants/{tenant_id}/voice-settings in app.api.admin_tenants). Clients can
# only read their current status above; they can no longer configure it themselves.


@router.get("/calls")
async def list_voice_calls(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[VoiceCallOut]:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await admin.list_calls(s, limit)


@router.get("/calls/{call_id}")
async def voice_call_detail(
    call_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> VoiceCallDetail:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await admin.call_detail(s, call_id)


@router.get("/stats")
async def voice_stats(
    days: int = Query(default=7, ge=1, le=90),
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> VoiceStatsOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await admin.stats(s, days)


@router.post("/calls/{call_id}/feedback")
async def voice_call_feedback(
    call_id: uuid.UUID,
    body: VoiceFeedbackIn,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> VoiceCallOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await admin.set_feedback(s, principal, call_id, body)
