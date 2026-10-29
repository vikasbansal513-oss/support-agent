from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ValidationError
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
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.integrations import gmail_oauth, service

router = APIRouter(prefix="/integrations", tags=["integrations"])
Factory = async_sessionmaker[AsyncSession]


class GmailConnectOut(BaseModel):
    url: str


@router.get("")
async def list_integrations(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[IntegrationOut]:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.list_integrations(s)


@router.post("/gmail/connect")
async def gmail_connect(
    principal: Principal = Depends(require_owner),  # noqa: B008
) -> GmailConnectOut:
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    return GmailConnectOut(url=gmail_oauth.build_auth_url(principal.tenant_id, principal.user_id))


@router.get("/gmail/callback", include_in_schema=False)
async def gmail_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    transport: httpx.AsyncBaseTransport | None = Depends(service.get_transport),  # noqa: B008
) -> RedirectResponse:
    base = get_settings().frontend_url.rstrip("/")
    result = "error"
    if code and state and not error:
        try:
            await gmail_oauth.complete_connect(factory, code, state, transport)
            result = "connected"
        except AppError:
            result = "error"
    return RedirectResponse(f"{base}/settings/integrations?gmail={result}", status_code=303)


@router.delete("/gmail")
async def gmail_disconnect(
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    transport: httpx.AsyncBaseTransport | None = Depends(service.get_transport),  # noqa: B008
) -> IntegrationOut:
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    await gmail_oauth.disconnect(factory, principal.tenant_id, principal.user_id, transport)
    return IntegrationOut(
        kind="gmail",
        configured=False,
        status="not_configured",
        config={},
        secret_hint=None,
        last_error_code=None,
        last_checked_at=None,
    )


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
    if kind not in service.COURIER_KINDS:
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
