import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.core.db import get_session_factory
from app.v1 import autonomy

router = APIRouter(prefix="/admin/tenants", tags=["admin-autonomy"])
Factory = async_sessionmaker[AsyncSession]


class AutonomyIn(BaseModel):
    tool: Literal["address_update", "order_cancel", "exchange_request", "refund_create"]
    level: Literal["off", "approval", "auto"]
    reason: str = Field(min_length=5, max_length=300)


class AutonomyOut(BaseModel):
    levels: dict[str, str]


@router.put("/{tenant_id}/autonomy")
async def set_autonomy(
    tenant_id: uuid.UUID,
    body: AutonomyIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> AutonomyOut:
    policy = await autonomy.set_level(factory, admin, tenant_id, body.tool, body.level, body.reason)
    return AutonomyOut(levels=policy.autonomy.model_dump(mode="json"))
