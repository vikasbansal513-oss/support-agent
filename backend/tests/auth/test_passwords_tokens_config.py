import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.auth import passwords
from app.auth.tokens import (
    create_access_token,
    create_mfa_token,
    csrf_token_for,
    decode_access_token,
    decode_mfa_token,
)
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from tests.auth.helpers import Clock


def test_password_hash_verify_and_wrong() -> None:
    stored = passwords.hash_password("correct-horse-battery")
    assert stored != "correct-horse-battery"
    assert passwords.verify_password("correct-horse-battery", stored)
    assert not passwords.verify_password("wrong-password-xx", stored)


def test_unknown_user_still_runs_hash_and_never_passes() -> None:
    assert passwords.verify_password("anything-goes-here", None) is False
    assert passwords.verify_password("dummy-password-for-timing-equalisation", None) is False


def test_password_length_bounds() -> None:
    passwords.validate_new_password("a" * 10)
    passwords.validate_new_password("a" * 128)
    for bad in ("a" * 9, "a" * 129, ""):
        with pytest.raises(AppError) as info:
            passwords.validate_new_password(bad)
        assert info.value.code == "E-AUTH-011"


def test_access_token_roundtrip(auth_env: None) -> None:
    user, tenant = uuid.uuid4(), uuid.uuid4()
    token, expires_in = create_access_token(
        user_id=user, realm="brand", role="owner", tenant_id=tenant, mfa_ok=True
    )
    claims = decode_access_token(token)
    assert (claims.user_id, claims.tenant_id, claims.role, claims.realm) == (
        user,
        tenant,
        "owner",
        "brand",
    )
    assert claims.mfa_ok and expires_in == 900


def test_realms_use_separate_secrets(auth_env: None) -> None:
    admin_token, _ = create_access_token(
        user_id=uuid.uuid4(), realm="admin", role="super_admin", tenant_id=None, mfa_ok=True
    )
    assert decode_access_token(admin_token).realm == "admin"
    settings = get_settings()
    forged = jwt.encode(
        {
            "typ": "access",
            "sub": str(uuid.uuid4()),
            "jti": "x",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "realm": "admin",
            "role": "super_admin",
            "tenant_id": None,
            "mfa_ok": True,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AppError):
        decode_access_token(forged)


def test_expired_tampered_and_wrong_type_rejected(auth_env: None, clock: Clock) -> None:
    clock.now = datetime.now(UTC) - timedelta(hours=1)
    old, _ = create_access_token(
        user_id=uuid.uuid4(), realm="brand", role="owner", tenant_id=uuid.uuid4(), mfa_ok=True
    )
    clock.now = datetime.now(UTC)
    with pytest.raises(AppError) as info:
        decode_access_token(old)
    assert info.value.code == "E-AUTH-004"
    fresh, _ = create_access_token(
        user_id=uuid.uuid4(), realm="brand", role="owner", tenant_id=uuid.uuid4(), mfa_ok=True
    )
    for bad in (fresh[:-3] + "abc", "not.a.jwt", ""):
        with pytest.raises(AppError):
            decode_access_token(bad)
    mfa, _ = create_mfa_token(uuid.uuid4(), "brand")
    with pytest.raises(AppError):
        decode_access_token(mfa)
    user = uuid.uuid4()
    mfa2, _ = create_mfa_token(user, "brand")
    assert decode_mfa_token(mfa2, "brand") == user
    with pytest.raises(AppError):
        decode_mfa_token(mfa2, "admin")
    with pytest.raises(AppError):
        decode_mfa_token(fresh, "brand")


def test_short_secret_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "short")
    monkeypatch.setenv("ADMIN_JWT_SECRET", "a" * 40)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            create_access_token(
                user_id=uuid.uuid4(), realm="brand", role="owner", tenant_id=None, mfa_ok=True
            )
    finally:
        get_settings.cache_clear()


def test_csrf_token_bound_to_refresh_token_and_realm(auth_env: None) -> None:
    a = csrf_token_for("refresh-a", "brand")
    assert a == csrf_token_for("refresh-a", "brand")
    assert a != csrf_token_for("refresh-b", "brand")
    assert a != csrf_token_for("refresh-a", "admin")


def test_admin_secret_must_differ_from_brand_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret="s" * 40, admin_jwt_secret="s" * 40)
    assert Settings(jwt_secret="s" * 40, admin_jwt_secret="t" * 40).admin_jwt_secret == "t" * 40


def test_admin_secret_has_no_fallback_to_brand_secret() -> None:
    settings = Settings(jwt_secret="s" * 40, admin_jwt_secret="")
    assert settings.admin_jwt_secret == ""


def test_allowed_origins_parsing() -> None:
    settings = Settings(
        frontend_url="https://a.test/", cors_origins="https://b.test, https://a.test"
    )
    assert settings.allowed_origins() == ["https://a.test", "https://b.test"]
    assert Settings().allowed_origins() == []
