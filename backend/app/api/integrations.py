from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_owner, require_user
from app.auth.service import Principal
from app.contracts.contracts import (
    CourierIn,
    GupshupIn,
    IntegrationKind,
    IntegrationOut,
    ProbeResultOut,
    ShopifyIn,
)
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.integrations import service

router = APIRouter(prefix="/integrations", tags=["integrations"])
Factory = async_sessionmaker[AsyncSession]


@router.get("")
async def list_integrations(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[IntegrationOut]:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.list_integrations(s)


@router.put("/shopify")
async def put_shopify(
    body: ShopifyIn,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> IntegrationOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.put_shopify(s, principal, body)


@router.put("/gupshup")
async def put_gupshup(
    body: GupshupIn,
    rotate_webhook: bool = Query(default=False),
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> IntegrationOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.put_gupshup(s, principal, body, rotate_webhook)


@router.put("/{kind}")
async def put_courier(
    kind: IntegrationKind,
    body: dict[str, Any],
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> IntegrationOut:
    if kind not in ("shiprocket", "delhivery"):
        raise AppError("E-NF-001")
    try:
        parsed = CourierIn.model_validate(body)
    except ValidationError as exc:
        raise AppError("E-VAL-001", detail="credential") from exc
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.put_courier(s, principal, kind, parsed)


@router.post("/{kind}/test")
async def test_integration(
    kind: IntegrationKind,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    transport: httpx.AsyncBaseTransport | None = Depends(service.get_transport),  # noqa: B008
) -> ProbeResultOut:
    return await service.run_probe(factory, principal, kind, transport)
