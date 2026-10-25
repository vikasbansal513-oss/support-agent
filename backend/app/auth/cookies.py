from fastapi import Request, Response

from app.auth.deps import COOKIE_NAMES
from app.auth.tokens import Realm
from app.core.config import get_settings
from app.core.errors import AppError


def set_refresh_cookie(response: Response, token: str, realm: Realm) -> None:
    settings = get_settings()
    name, path = COOKIE_NAMES[realm]
    response.set_cookie(
        name,
        token,
        max_age=settings.refresh_token_days * 86400,
        path=path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def clear_refresh_cookie(response: Response, realm: Realm) -> None:
    settings = get_settings()
    name, path = COOKIE_NAMES[realm]
    response.delete_cookie(
        name,
        path=path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def read_refresh_cookie(request: Request, realm: Realm) -> str:
    token = request.cookies.get(COOKIE_NAMES[realm][0], "")
    if not token:
        raise AppError("E-AUTH-004")
    return token
