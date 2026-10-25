from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api._session import do_logout, do_refresh, login_response
from app.auth import service
from app.auth.deps import client_ip
from app.contracts.contracts import LoginIn, MfaVerifyIn
from app.core.db import get_session_factory

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])
Factory = async_sessionmaker[AsyncSession]


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    outcome = await service.login(
        factory, email=body.email, password=body.password, ip=client_ip(request), realm="admin"
    )
    return login_response(outcome, "admin")


@router.post("/mfa/verify")
async def mfa_verify(
    body: MfaVerifyIn,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    result = await service.mfa_verify(
        factory, mfa_token=body.mfa_token, code=body.code, ip=client_ip(request), realm="admin"
    )
    return login_response(result, "admin")


@router.post("/refresh")
async def refresh(
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    return await do_refresh(request, factory, "admin")


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    return await do_logout(request, factory, "admin")
