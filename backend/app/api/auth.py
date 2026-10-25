from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api._session import do_logout, do_refresh, login_response
from app.auth import mfa, service
from app.auth.deps import client_ip, current_principal
from app.auth.service import Principal
from app.contracts.contracts import (
    ChangePasswordIn,
    CodeIn,
    EmailIn,
    LoginIn,
    MfaSetupOut,
    MfaStatusOut,
    MfaVerifyIn,
    RecoveryCodesOut,
    ResetIn,
    SignupIn,
    StatusOut,
    TokenIn,
    UserOut,
)
from app.core.db import get_session_factory, session_scope
from app.core.mail import Mailer, get_mailer

router = APIRouter(prefix="/auth", tags=["auth"])
Factory = async_sessionmaker[AsyncSession]


@router.post("/signup", status_code=202)
async def signup(
    body: SignupIn,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    mailer: Mailer = Depends(get_mailer),  # noqa: B008
) -> StatusOut:
    await service.signup(
        factory,
        mailer,
        brand_name=body.brand_name,
        name=body.name,
        email=str(body.email),
        password=body.password,
        ip=client_ip(request),
    )
    return StatusOut(status="verification_sent")


@router.post("/verify-email")
async def verify_email(
    body: TokenIn,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StatusOut:
    await service.verify_email(factory, body.token)
    return StatusOut(status="verified")


@router.post("/resend-verification", status_code=202)
async def resend_verification(
    body: EmailIn,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    mailer: Mailer = Depends(get_mailer),  # noqa: B008
) -> StatusOut:
    await service.resend_verification(factory, mailer, body.email, client_ip(request))
    return StatusOut(status="accepted")


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    outcome = await service.login(
        factory, email=body.email, password=body.password, ip=client_ip(request), realm="brand"
    )
    return login_response(outcome, "brand")


@router.post("/mfa/verify")
async def mfa_verify(
    body: MfaVerifyIn,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    result = await service.mfa_verify(
        factory, mfa_token=body.mfa_token, code=body.code, ip=client_ip(request), realm="brand"
    )
    return login_response(result, "brand")


@router.post("/refresh")
async def refresh(
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    return await do_refresh(request, factory, "brand")


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    return await do_logout(request, factory, "brand")


@router.post("/forgot-password", status_code=202)
async def forgot_password(
    body: EmailIn,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    mailer: Mailer = Depends(get_mailer),  # noqa: B008
) -> StatusOut:
    await service.forgot_password(factory, mailer, body.email, client_ip(request))
    return StatusOut(status="accepted")


@router.post("/reset-password")
async def reset_password(
    body: ResetIn,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StatusOut:
    await service.reset_password(factory, body.token, body.new_password)
    return StatusOut(status="password_changed")


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn,
    principal: Principal = Depends(current_principal),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> StatusOut:
    await service.change_password(factory, principal, body.current_password, body.new_password)
    return StatusOut(status="password_changed")


@router.get("/me")
async def me(principal: Principal = Depends(current_principal)) -> UserOut:  # noqa: B008
    return UserOut(
        id=principal.user_id,
        email=principal.email,
        name=principal.name,
        role=principal.role,
        tenant_id=principal.tenant_id,
        mfa_ok=principal.mfa_ok,
    )


@router.get("/mfa")
async def mfa_status(
    principal: Principal = Depends(current_principal),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> MfaStatusOut:
    async with session_scope(factory, super_admin=True) as session:
        state = await mfa.get_state(session, principal.user_id)
    return MfaStatusOut(
        enabled=state.confirmed,
        required=principal.realm == "admin",
        recovery_remaining=state.recovery_remaining,
    )


@router.post("/mfa/setup")
async def mfa_setup(
    principal: Principal = Depends(current_principal),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> MfaSetupOut:
    async with session_scope(factory, super_admin=True) as session:
        secret, uri = await mfa.begin_setup(session, principal.user_id, principal.email)
    return MfaSetupOut(secret=secret, otpauth_uri=uri)


@router.post("/mfa/confirm")
async def mfa_confirm(
    body: CodeIn,
    principal: Principal = Depends(current_principal),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> RecoveryCodesOut:
    codes = await service.confirm_mfa(factory, principal, body.code)
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/mfa/recovery-codes")
async def mfa_recovery_codes(
    body: CodeIn,
    principal: Principal = Depends(current_principal),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> RecoveryCodesOut:
    async with session_scope(factory, super_admin=True) as session:
        codes = await mfa.regenerate_recovery_codes(session, principal.user_id, body.code)
    return RecoveryCodesOut(recovery_codes=codes)
