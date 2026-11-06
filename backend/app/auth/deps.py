from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import ratelimit, service
from app.auth.service import Principal
from app.auth.tokens import Realm, decode_access_token
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

COOKIE_NAMES: dict[Realm, tuple[str, str]] = {
    # Path is "/" (not the backend's own "/auth" / "/admin/auth" route prefix):
    # when the frontend reaches us through the Vercel same-origin proxy
    # (rewrite "/api/:path*" -> backend), the browser sees the Set-Cookie as a
    # response to "https://<frontend>/api/auth/...", so a cookie scoped to
    # "/auth" never matches later requests to "/api/auth/refresh" and the
    # cookie is silently never sent back. Realm isolation is by cookie NAME
    # (sa_refresh vs sa_admin_refresh), not by Path, so "/" is safe for both.
    "brand": ("sa_refresh", "/"),
    "admin": ("sa_admin_refresh", "/"),
}

# Readable (non-httponly) twin of COOKIE_NAMES: holds the CSRF value so a
# fresh page load (empty JS memory) can still send X-CSRF-Token on the
# first /auth/refresh call. See cookies.set_csrf_cookie.
CSRF_COOKIE_NAMES: dict[Realm, tuple[str, str]] = {
    "brand": ("sa_csrf", "/"),
    "admin": ("sa_admin_csrf", "/"),
}


def client_ip(request: Request) -> str:
    hops = get_settings().trusted_proxy_hops
    forwarded = [
        p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
    ]
    if hops and len(forwarded) >= hops:
        return forwarded[-hops]
    return request.client.host if request.client else "unknown"


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AppError("E-AUTH-004")
    return token.strip()


async def current_principal(
    request: Request,
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
) -> Principal:
    claims = decode_access_token(_bearer(request))
    principal = await service.load_principal(factory, claims)
    # Per-tenant traffic ceiling on every authenticated dashboard request: a noisy tenant
    # only throttles itself (its own bucket in rate_limits), never anyone else's.
    if principal.tenant_id is not None:
        await ratelimit.hit_tenant_api(factory, principal.tenant_id)
    return principal


async def require_user(principal: Principal = Depends(current_principal)) -> Principal:  # noqa: B008
    if principal.realm != "brand":
        raise AppError("E-AUTH-007")
    if not principal.mfa_ok:
        raise AppError("E-AUTH-005")
    return principal


async def require_owner(principal: Principal = Depends(require_user)) -> Principal:  # noqa: B008
    if principal.role != "owner":
        raise AppError("E-AUTH-007")
    return principal


async def require_admin(principal: Principal = Depends(current_principal)) -> Principal:  # noqa: B008
    if principal.realm != "admin":
        raise AppError("E-ADM-001")
    if not principal.mfa_ok:
        raise AppError("E-AUTH-005")
    return principal


async def tenant_session(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
) -> AsyncIterator[AsyncSession]:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        yield session
