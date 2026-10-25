import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import jwt

from app.core import timeutil
from app.core.config import Settings, get_settings
from app.core.errors import AppError

Realm = Literal["brand", "admin"]
ALGORITHM = "HS256"
MFA_TOKEN_MINUTES = 5
EMAIL_TOKEN_TTL = {"verify": timedelta(hours=24), "reset": timedelta(hours=1)}


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    realm: Realm
    role: str
    tenant_id: uuid.UUID | None
    mfa_ok: bool
    issued_at: int
    jti: str


def _secret(realm: Realm, settings: Settings) -> str:
    secret = settings.admin_jwt_secret if realm == "admin" else settings.jwt_secret
    if len(secret) < 32:
        raise RuntimeError(f"{'ADMIN_JWT_SECRET' if realm == 'admin' else 'JWT_SECRET'} too short")
    return secret


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_for(refresh_token: str, realm: Realm, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    key = _secret(realm, settings).encode()
    return hmac.new(key, f"csrf:{refresh_token}".encode(), hashlib.sha256).hexdigest()


def _ensure_not_expired(payload: dict[str, object]) -> None:
    exp = payload.get("exp")
    if not isinstance(exp, int | float) or exp <= timeutil.utc_now().timestamp():
        raise AppError("E-AUTH-004")


def create_access_token(
    *,
    user_id: uuid.UUID,
    realm: Realm,
    role: str,
    tenant_id: uuid.UUID | None,
    mfa_ok: bool,
    settings: Settings | None = None,
) -> tuple[str, int]:
    settings = settings or get_settings()
    now = timeutil.utc_now()
    expires = timedelta(minutes=settings.access_token_minutes)
    payload = {
        "typ": "access",
        "sub": str(user_id),
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
        "realm": realm,
        "role": role,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "mfa_ok": mfa_ok,
    }
    return jwt.encode(payload, _secret(realm, settings), algorithm=ALGORITHM), int(
        expires.total_seconds()
    )


def decode_access_token(token: str, settings: Settings | None = None) -> AccessClaims:
    settings = settings or get_settings()
    for realm in ("brand", "admin"):
        try:
            payload = jwt.decode(
                token,
                _secret(realm, settings),
                algorithms=[ALGORITHM],
                options={
                    "require": ["exp", "iat", "sub", "jti"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except (jwt.InvalidTokenError, RuntimeError):
            continue
        _ensure_not_expired(payload)
        if payload.get("typ") != "access" or payload.get("realm") != realm:
            raise AppError("E-AUTH-004")
        try:
            return AccessClaims(
                user_id=uuid.UUID(payload["sub"]),
                realm=realm,
                role=str(payload["role"]),
                tenant_id=uuid.UUID(payload["tenant_id"]) if payload.get("tenant_id") else None,
                mfa_ok=bool(payload["mfa_ok"]),
                issued_at=int(payload["iat"]),
                jti=str(payload["jti"]),
            )
        except (KeyError, ValueError) as exc:
            raise AppError("E-AUTH-004") from exc
    raise AppError("E-AUTH-004")


def create_mfa_token(
    user_id: uuid.UUID, realm: Realm, settings: Settings | None = None
) -> tuple[str, int]:
    settings = settings or get_settings()
    now = timeutil.utc_now()
    expires = timedelta(minutes=MFA_TOKEN_MINUTES)
    payload = {
        "typ": "mfa",
        "sub": str(user_id),
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
        "realm": realm,
    }
    return jwt.encode(payload, _secret(realm, settings), algorithm=ALGORITHM), int(
        expires.total_seconds()
    )


def decode_mfa_token(token: str, realm: Realm, settings: Settings | None = None) -> uuid.UUID:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            _secret(realm, settings),
            algorithms=[ALGORITHM],
            options={
                "require": ["exp", "iat", "sub", "jti"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        _ensure_not_expired(payload)
        if payload.get("typ") != "mfa" or payload.get("realm") != realm:
            raise AppError("E-AUTH-004")
        return uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise AppError("E-AUTH-004") from exc


def email_token_expiry(purpose: Literal["verify", "reset"], now: datetime) -> datetime:
    return now + EMAIL_TOKEN_TTL[purpose]
