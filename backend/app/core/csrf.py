import hmac

from fastapi import Request

from app.auth.tokens import Realm, csrf_token_for
from app.core.config import get_settings
from app.core.errors import AppError


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or origin not in get_settings().allowed_origins():
        raise AppError("E-AUTH-010", detail="origin not allowed")


def check_csrf(request: Request, refresh_token: str, realm: Realm) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    expected = csrf_token_for(refresh_token, realm)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise AppError("E-AUTH-010", detail="csrf mismatch")
