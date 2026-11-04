import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_owner
from app.auth.service import Principal
from app.contracts.contracts import PrivacyRequestIn, PrivacyRequestOut, PublicNoticeOut, ReasonIn
from app.core.db import get_session_factory
from app.core.errors import AppError
from app.privacy import notices
from app.privacy import service as privacy

router = APIRouter(tags=["privacy"])


def _tenant(principal: Principal) -> uuid.UUID:
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    return principal.tenant_id


Factory = async_sessionmaker[AsyncSession]


@router.get("/privacy/requests")
async def list_requests(
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[PrivacyRequestOut]:
    return await privacy.list_requests(factory, _tenant(principal))


@router.post("/privacy/requests", status_code=201)
async def create_request(
    body: PrivacyRequestIn,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PrivacyRequestOut:
    return await privacy.create_request(
        factory,
        _tenant(principal),
        principal.user_id,
        body.subject_ref,
        body.kind,
        correction_field=body.correction_field,
        correction_value=body.correction_value,
    )


@router.post("/privacy/requests/{request_id}/approve")
async def approve_request(
    request_id: uuid.UUID,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PrivacyRequestOut:
    return await privacy.decide_correction(
        factory, _tenant(principal), principal.user_id, request_id, approve=True
    )


@router.post("/privacy/requests/{request_id}/reject")
async def reject_request(
    request_id: uuid.UUID,
    body: ReasonIn,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PrivacyRequestOut:
    return await privacy.decide_correction(
        factory, _tenant(principal), principal.user_id, request_id, approve=False, reason=body.reason
    )


@router.get("/privacy/export")
async def export(
    subject_ref: str,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> JSONResponse:
    data: dict[str, Any] = await privacy.export_customer(
        factory, _tenant(principal), principal.user_id, subject_ref
    )
    return JSONResponse(
        data, headers={"Content-Disposition": 'attachment; filename="customer-export.json"'}
    )


@router.get("/public/privacy-notice")
async def privacy_notice() -> PublicNoticeOut:
    return notices.privacy_notice()


@router.get("/public/terms")
async def terms() -> PublicNoticeOut:
    return notices.terms()
