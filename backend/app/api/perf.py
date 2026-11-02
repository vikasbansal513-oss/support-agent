from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_user
from app.auth.service import Principal
from app.contracts.contracts import PagePerfBeaconIn, StatusOut
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.monitoring.perf_ingest import record_beacon

router = APIRouter(tags=["perf"])
Factory = async_sessionmaker[AsyncSession]


@router.post("/perf/beacon")
async def perf_beacon(
    body: PagePerfBeaconIn,
    request: Request,
    user: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StatusOut:
    if user.tenant_id is None:
        raise AppError("E-VAL-001", detail="no tenant")
    async with session_scope(factory, tenant_id=user.tenant_id, user_id=user.user_id) as session:
        await record_beacon(session, user.tenant_id, body, request.headers.get("user-agent"))
    return StatusOut(status="ok")
