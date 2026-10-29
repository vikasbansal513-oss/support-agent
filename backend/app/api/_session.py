from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import service
from app.auth.cookies import (
    clear_csrf_cookie,
    clear_refresh_cookie,
    read_refresh_cookie,
    set_csrf_cookie,
    set_refresh_cookie,
)
from app.auth.service import AuthResult, MfaChallenge
from app.auth.tokens import Realm
from app.contracts.contracts import LoginOut, MfaChallengeOut, UserOut
from app.core.csrf import check_csrf, check_origin


def to_login_out(result: AuthResult) -> LoginOut:
    user = result.user
    return LoginOut(
        access_token=result.access_token,
        expires_in=result.expires_in,
        csrf_token=result.csrf_token,
        user=UserOut(
            id=user.user_id,
            email=user.email,
            name=user.name,
            role=user.role,
            tenant_id=user.tenant_id,
            mfa_ok=user.mfa_ok,
        ),
    )


def login_response(outcome: AuthResult | MfaChallenge, realm: Realm) -> Response:
    body: BaseModel
    if isinstance(outcome, MfaChallenge):
        body = MfaChallengeOut(mfa_token=outcome.mfa_token, expires_in=outcome.expires_in)
        return JSONResponse(body.model_dump(mode="json"))
    response = JSONResponse(to_login_out(outcome).model_dump(mode="json"))
    set_refresh_cookie(response, outcome.refresh_token, realm)
    set_csrf_cookie(response, outcome.csrf_token, realm)
    return response


async def do_refresh(
    request: Request, factory: async_sessionmaker[AsyncSession], realm: Realm
) -> Response:
    check_origin(request)
    token = read_refresh_cookie(request, realm)
    check_csrf(request, token, realm)
    result = await service.refresh(factory, token, realm)
    response = JSONResponse(to_login_out(result).model_dump(mode="json"))
    set_refresh_cookie(response, result.refresh_token, realm)
    set_csrf_cookie(response, result.csrf_token, realm)
    return response


async def do_logout(
    request: Request, factory: async_sessionmaker[AsyncSession], realm: Realm
) -> Response:
    check_origin(request)
    token = read_refresh_cookie(request, realm)
    check_csrf(request, token, realm)
    await service.logout(factory, token)
    response = Response(status_code=204)
    clear_refresh_cookie(response, realm)
    clear_csrf_cookie(response, realm)
    return response
