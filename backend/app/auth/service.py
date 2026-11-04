import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import mfa, passwords, ratelimit
from app.auth.tokens import (
    AccessClaims,
    Realm,
    create_access_token,
    create_mfa_token,
    csrf_token_for,
    decode_mfa_token,
    email_token_expiry,
    new_opaque_token,
    sha256_hex,
)
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError
from app.core.mail import Mailer, send_safely

Factory = async_sessionmaker[AsyncSession]
RESEND_GAP = timedelta(seconds=60)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    name: str
    realm: Realm
    role: str
    tenant_id: uuid.UUID | None
    mfa_ok: bool
    tenant_name: str | None = None


@dataclass(frozen=True)
class AuthResult:
    access_token: str
    expires_in: int
    refresh_token: str
    csrf_token: str
    user: Principal


@dataclass(frozen=True)
class MfaChallenge:
    mfa_token: str
    expires_in: int


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30].strip("-") or "brand"
    return f"{base}-{secrets.token_hex(3)}"


def _link(path: str, token: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{path}?token={token}"


async def _create_email_token(
    session: AsyncSession, user_id: uuid.UUID, purpose: Literal["verify", "reset"]
) -> str:
    token = new_opaque_token()
    await session.execute(
        text(
            "UPDATE email_tokens SET used_at = :now "
            "WHERE user_id = :u AND purpose = :p AND used_at IS NULL"
        ),
        {"now": timeutil.utc_now(), "u": user_id, "p": purpose},
    )
    await session.execute(
        text(
            "INSERT INTO email_tokens (user_id, purpose, token_hash, expires_at, created_at) "
            "VALUES (:u, :p, :h, :exp, :now)"
        ),
        {
            "u": user_id,
            "p": purpose,
            "h": sha256_hex(token),
            "exp": email_token_expiry(purpose, timeutil.utc_now()),
            "now": timeutil.utc_now(),
        },
    )
    return token


async def _consume_email_token(
    session: AsyncSession, token: str, purpose: Literal["verify", "reset"]
) -> uuid.UUID:
    now = timeutil.utc_now()
    row = (
        await session.execute(
            text(
                "UPDATE email_tokens SET used_at = :now WHERE token_hash = :h AND purpose = :p "
                "AND used_at IS NULL AND expires_at > :now RETURNING user_id"
            ),
            {"now": now, "h": sha256_hex(token), "p": purpose},
        )
    ).first()
    if row is None:
        raise AppError("E-AUTH-008")
    user_id: uuid.UUID = row[0]
    return user_id


async def signup(
    factory: Factory,
    mailer: Mailer,
    *,
    brand_name: str,
    name: str,
    email: str,
    password: str,
    ip: str,
) -> None:
    email = normalize_email(email)
    await ratelimit.hit(factory, "signup_ip", ip)
    await ratelimit.hit(factory, "signup_email", email)
    passwords.validate_new_password(password)
    password_hash = passwords.hash_password(password)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = get_settings()
    try:
        async with session_scope(factory, super_admin=True) as session:
            exists = (
                await session.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": email})
            ).first()
            if exists:
                raise AppError("E-AUTH-012")
            await session.execute(
                text("INSERT INTO tenants (id, name, slug, mode) VALUES (:i, :n, :s, :m)"),
                {
                    "i": tenant_id,
                    "n": brand_name.strip(),
                    "s": _slugify(brand_name),
                    "m": settings.default_tenant_mode,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, name, password_hash, password_changed_at) "
                    "VALUES (:i, :e, :n, :p, :now)"
                ),
                {
                    "i": user_id,
                    "e": email,
                    "n": name.strip(),
                    "p": password_hash,
                    "now": timeutil.utc_now(),
                },
            )
            await session.execute(
                text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'owner')"),
                {"t": tenant_id, "u": user_id},
            )
            token = await _create_email_token(session, user_id, "verify")
            await record(
                session,
                AuditEvent(
                    action="auth.signup",
                    entity_type="tenant",
                    entity_id=str(tenant_id),
                    actor_user_id=user_id,
                    actor_role="owner",
                ),
                tenant_id=tenant_id,
            )
    except IntegrityError as exc:
        raise AppError("E-AUTH-012", detail="signup race") from exc
    await send_safely(
        mailer,
        email,
        "Email verify karein",
        f"Namaste {name.strip()},\n\nApna email verify karne ke liye ye link kholein "
        f"(24 ghante tak chalega):\n{_link('/verify-email', token)}\n",
    )


async def verify_email(factory: Factory, token: str) -> None:
    async with session_scope(factory, super_admin=True) as session:
        user_id = await _consume_email_token(session, token, "verify")
        await session.execute(
            text(
                "UPDATE users SET email_verified_at = COALESCE(email_verified_at, :now), "
                "updated_at = :now WHERE id = :u"
            ),
            {"now": timeutil.utc_now(), "u": user_id},
        )


async def resend_verification(factory: Factory, mailer: Mailer, email: str, ip: str) -> None:
    email = normalize_email(email)
    await ratelimit.hit(factory, "resend_email", email)
    now = timeutil.utc_now()
    token: str | None = None
    name = ""
    async with session_scope(factory, super_admin=True) as session:
        user = (
            await session.execute(
                text(
                    "SELECT id, name FROM users WHERE email = :e AND is_active "
                    "AND email_verified_at IS NULL"
                ),
                {"e": email},
            )
        ).first()
        if user is not None:
            recent = (
                await session.execute(
                    text(
                        "SELECT 1 FROM email_tokens WHERE user_id = :u AND purpose = 'verify' "
                        "AND created_at > :since"
                    ),
                    {"u": user[0], "since": now - RESEND_GAP},
                )
            ).first()
            if recent is None:
                token = await _create_email_token(session, user[0], "verify")
                name = user[1]
    if token is not None:
        await send_safely(
            mailer,
            email,
            "Email verify karein",
            f"Namaste {name},\n\nVerify link (24 ghante):\n{_link('/verify-email', token)}\n",
        )


async def forgot_password(factory: Factory, mailer: Mailer, email: str, ip: str) -> None:
    email = normalize_email(email)
    await ratelimit.hit(factory, "forgot_email", email)
    token: str | None = None
    name = ""
    async with session_scope(factory, super_admin=True) as session:
        user = (
            await session.execute(
                text("SELECT id, name FROM users WHERE email = :e AND is_active"), {"e": email}
            )
        ).first()
        if user is not None:
            token = await _create_email_token(session, user[0], "reset")
            name = user[1]
    if token is not None:
        await send_safely(
            mailer,
            email,
            "Password reset",
            f"Namaste {name},\n\nPassword badalne ka link (1 ghanta):\n"
            f"{_link('/reset-password', token)}\n\nAgar ye aapne nahi manga to ignore karein.\n",
        )


async def _set_password(session: AsyncSession, user_id: uuid.UUID, password: str) -> None:
    now = timeutil.utc_now()
    await session.execute(
        text(
            "UPDATE users SET password_hash = :p, password_changed_at = :now, failed_attempts = 0, "
            "locked_until = NULL, updated_at = :now WHERE id = :u"
        ),
        {"p": passwords.hash_password(password), "now": now, "u": user_id},
    )
    await session.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = :now WHERE user_id = :u AND revoked_at IS NULL"
        ),
        {"now": now, "u": user_id},
    )


async def reset_password(factory: Factory, token: str, new_password: str) -> None:
    passwords.validate_new_password(new_password)
    async with session_scope(factory, super_admin=True) as session:
        user_id = await _consume_email_token(session, token, "reset")
        await _set_password(session, user_id, new_password)
        await record(
            session,
            AuditEvent(action="auth.password_reset", entity_type="user", entity_id=str(user_id)),
        )


async def change_password(
    factory: Factory, principal: Principal, current_password: str, new_password: str
) -> None:
    passwords.validate_new_password(new_password)
    async with session_scope(factory, super_admin=True) as session:
        stored = (
            await session.execute(
                text("SELECT password_hash FROM users WHERE id = :u"), {"u": principal.user_id}
            )
        ).scalar_one_or_none()
        ok = passwords.verify_password(current_password, stored)
        if ok:
            await _set_password(session, principal.user_id, new_password)
            await record(
                session,
                AuditEvent(
                    action="auth.password_changed",
                    entity_type="user",
                    entity_id=str(principal.user_id),
                    actor_user_id=principal.user_id,
                ),
                tenant_id=principal.tenant_id,
            )
    if not ok:
        raise AppError("E-AUTH-001")


async def _principal_info(
    session: AsyncSession, user_id: uuid.UUID, realm: Realm
) -> tuple[str, uuid.UUID | None, str | None]:
    if realm == "admin":
        row = (
            await session.execute(
                text("SELECT 1 FROM platform_admins WHERE user_id = :u"), {"u": user_id}
            )
        ).first()
        if row is None:
            raise AppError("E-AUTH-007")
        return "super_admin", None, None
    # tenant_id is taken from this user's own membership row, so the brand name
    # fetched below is always the authenticated user's own tenant - never another
    # client's.
    row = (
        await session.execute(
            text(
                "SELECT m.role, m.tenant_id, t.status, t.name FROM memberships m "
                "JOIN tenants t ON t.id = m.tenant_id WHERE m.user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
    if row is None:
        raise AppError("E-AUTH-007")
    if row[2] != "active":
        raise AppError("E-AUTH-009")
    return str(row[0]), row[1], row[3]


async def _issue_session(
    session: AsyncSession, user: Any, realm: Realm, mfa_ok: bool
) -> AuthResult:
    role, tenant_id, tenant_name = await _principal_info(session, user.id, realm)
    settings = get_settings()
    now = timeutil.utc_now()
    refresh_token = new_opaque_token()
    await session.execute(
        text(
            "INSERT INTO refresh_tokens "
            "(user_id, realm, family_id, token_hash, expires_at, created_at) "
            "VALUES (:u, :r, :f, :h, :exp, :now)"
        ),
        {
            "u": user.id,
            "r": "admin" if realm == "admin" else "brand",
            "f": uuid.uuid4(),
            "h": sha256_hex(refresh_token),
            "exp": now + timedelta(days=settings.refresh_token_days),
            "now": now,
        },
    )
    return _build_result(user, realm, role, tenant_id, mfa_ok, refresh_token, tenant_name)


def _build_result(
    user: Any,
    realm: Realm,
    role: str,
    tenant_id: uuid.UUID | None,
    mfa_ok: bool,
    refresh_token: str,
    tenant_name: str | None = None,
) -> AuthResult:
    access, expires_in = create_access_token(
        user_id=user.id, realm=realm, role=role, tenant_id=tenant_id, mfa_ok=mfa_ok
    )
    return AuthResult(
        access_token=access,
        expires_in=expires_in,
        refresh_token=refresh_token,
        csrf_token=csrf_token_for(refresh_token, realm),
        user=Principal(user.id, user.email, user.name, realm, role, tenant_id, mfa_ok, tenant_name),
    )


_USER_BY_EMAIL = text(
    "SELECT id, email, name, password_hash, email_verified_at, failed_attempts, locked_until, "
    "is_active FROM users WHERE email = :e"
)
_USER_BY_ID = text(
    "SELECT id, email, name, password_hash, email_verified_at, failed_attempts, locked_until, "
    "is_active FROM users WHERE id = :u"
)


async def _record_failure(factory: Factory, user_id: uuid.UUID, now: datetime) -> bool:
    settings = get_settings()
    async with session_scope(factory, super_admin=True) as session:
        locked = (
            await session.execute(
                text(
                    "UPDATE users SET "
                    "locked_until = CASE WHEN failed_attempts + 1 >= :max "
                    "THEN CAST(:until AS timestamptz) END, "
                    "failed_attempts = CASE WHEN failed_attempts + 1 >= :max THEN 0 "
                    "ELSE failed_attempts + 1 END, updated_at = :now "
                    "WHERE id = :u RETURNING locked_until IS NOT NULL"
                ),
                {
                    "max": settings.lockout_max_attempts,
                    "until": now + timedelta(minutes=settings.lockout_minutes),
                    "now": now,
                    "u": user_id,
                },
            )
        ).scalar_one()
    return bool(locked)


async def login(
    factory: Factory, *, email: str, password: str, ip: str, realm: Realm
) -> AuthResult | MfaChallenge:
    email = normalize_email(email)
    await ratelimit.hit(factory, "login_ip", ip)
    await ratelimit.hit(factory, "login_email", email)
    now = timeutil.utc_now()
    async with session_scope(factory, super_admin=True) as session:
        user = (await session.execute(_USER_BY_EMAIL, {"e": email})).first()
    password_ok = passwords.verify_password(password, user.password_hash if user else None)
    if user is None:
        raise AppError("E-AUTH-001")
    if user.locked_until is not None and user.locked_until > now:
        raise AppError("E-AUTH-002")
    if not password_ok:
        if await _record_failure(factory, user.id, now):
            raise AppError("E-AUTH-002")
        raise AppError("E-AUTH-001")
    if not user.is_active:
        raise AppError("E-AUTH-001")
    if user.email_verified_at is None:
        raise AppError("E-AUTH-003")
    async with session_scope(factory, super_admin=True) as session:
        if user.failed_attempts or user.locked_until:
            await session.execute(
                text(
                    "UPDATE users SET failed_attempts = 0, locked_until = NULL, updated_at = :now "
                    "WHERE id = :u"
                ),
                {"now": now, "u": user.id},
            )
        if realm == "admin":
            is_admin = (
                await session.execute(
                    text("SELECT 1 FROM platform_admins WHERE user_id = :u"), {"u": user.id}
                )
            ).first()
            if is_admin is None:
                raise AppError("E-AUTH-001")
        else:
            await _principal_info(session, user.id, realm)
        state = await mfa.get_state(session, user.id)
        if state.confirmed:
            token, expires_in = create_mfa_token(user.id, realm)
            return MfaChallenge(mfa_token=token, expires_in=expires_in)
        return await _issue_session(session, user, realm, mfa_ok=realm != "admin")


async def mfa_verify(
    factory: Factory, *, mfa_token: str, code: str, ip: str, realm: Realm
) -> AuthResult:
    await ratelimit.hit(factory, "mfa_ip", ip)
    user_id = decode_mfa_token(mfa_token, realm)
    await ratelimit.hit(factory, "mfa_user", str(user_id))
    async with session_scope(factory, super_admin=True) as session:
        user = (await session.execute(_USER_BY_ID, {"u": user_id})).first()
        if user is None or not user.is_active:
            raise AppError("E-AUTH-004")
        result: AuthResult | None = None
        if await mfa.verify_login_code(session, user_id, code):
            result = await _issue_session(session, user, realm, mfa_ok=True)
    if result is None:
        raise AppError("E-AUTH-005")
    return result


async def refresh(factory: Factory, refresh_token: str, realm: Realm) -> AuthResult:
    now = timeutil.utc_now()
    failure: AppError | None = None
    result: AuthResult | None = None
    async with session_scope(factory, super_admin=True) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, user_id, realm, family_id, expires_at, revoked_at "
                    "FROM refresh_tokens WHERE token_hash = :h FOR UPDATE"
                ),
                {"h": sha256_hex(refresh_token)},
            )
        ).first()
        if row is None or row.realm != realm:
            failure = AppError("E-AUTH-004")
        else:
            user = (await session.execute(_USER_BY_ID, {"u": row.user_id})).first()
            revoke_family = row.revoked_at is not None or row.expires_at <= now
            if not revoke_family and (user is None or not user.is_active):
                revoke_family = True
            role = ""
            tenant_id: uuid.UUID | None = None
            tenant_name: str | None = None
            if not revoke_family:
                try:
                    role, tenant_id, tenant_name = await _principal_info(session, row.user_id, realm)
                except AppError:
                    revoke_family = True
            if revoke_family:
                await session.execute(
                    text(
                        "UPDATE refresh_tokens SET revoked_at = :now "
                        "WHERE family_id = :f AND revoked_at IS NULL"
                    ),
                    {"now": now, "f": row.family_id},
                )
                failure = AppError("E-AUTH-004", detail="refresh rejected")
            else:
                state = await mfa.get_state(session, row.user_id)
                mfa_ok = state.confirmed if realm == "admin" else True
                new_token = new_opaque_token()
                new_id = uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO refresh_tokens "
                        "(id, user_id, realm, family_id, token_hash, expires_at, created_at) "
                        "VALUES (:i, :u, :r, :f, :h, :exp, :now)"
                    ),
                    {
                        "i": new_id,
                        "u": row.user_id,
                        "r": realm,
                        "f": row.family_id,
                        "h": sha256_hex(new_token),
                        "exp": now + timedelta(days=get_settings().refresh_token_days),
                        "now": now,
                    },
                )
                await session.execute(
                    text(
                        "UPDATE refresh_tokens SET revoked_at = :now, replaced_by = :n "
                        "WHERE id = :i"
                    ),
                    {"now": now, "n": new_id, "i": row.id},
                )
                result = _build_result(user, realm, role, tenant_id, mfa_ok, new_token, tenant_name)
    if failure is not None or result is None:
        raise failure or AppError("E-AUTH-004")
    return result


async def logout(factory: Factory, refresh_token: str) -> None:
    async with session_scope(factory, super_admin=True) as session:
        await session.execute(
            text(
                "UPDATE refresh_tokens SET revoked_at = :now "
                "WHERE revoked_at IS NULL AND family_id = "
                "(SELECT family_id FROM refresh_tokens WHERE token_hash = :h)"
            ),
            {"now": timeutil.utc_now(), "h": sha256_hex(refresh_token)},
        )


async def load_principal(factory: Factory, claims: AccessClaims) -> Principal:
    async with session_scope(factory, super_admin=True) as session:
        user = (
            await session.execute(
                text(
                    "SELECT id, email, name, is_active, password_changed_at "
                    "FROM users WHERE id = :u"
                ),
                {"u": claims.user_id},
            )
        ).first()
        if user is None or not user.is_active:
            raise AppError("E-AUTH-004")
        if user.password_changed_at is not None and claims.issued_at < int(
            user.password_changed_at.timestamp()
        ):
            raise AppError("E-AUTH-004")
        role, tenant_id, tenant_name = await _principal_info(session, claims.user_id, claims.realm)
    if tenant_id != claims.tenant_id:
        raise AppError("E-AUTH-004")
    return Principal(
        user.id, user.email, user.name, claims.realm, role, tenant_id, claims.mfa_ok, tenant_name
    )


async def confirm_mfa(factory: Factory, principal: Principal, code: str) -> list[str]:
    await ratelimit.hit(factory, "mfa_user", str(principal.user_id))
    async with session_scope(factory, super_admin=True) as session:
        codes = await mfa.confirm_setup(session, principal.user_id, code)
        await session.execute(
            text(
                "UPDATE refresh_tokens SET revoked_at = :now "
                "WHERE user_id = :u AND revoked_at IS NULL"
            ),
            {"now": timeutil.utc_now(), "u": principal.user_id},
        )
        await record(
            session,
            AuditEvent(
                action="auth.mfa_enabled",
                entity_type="user",
                entity_id=str(principal.user_id),
                actor_user_id=principal.user_id,
            ),
            tenant_id=principal.tenant_id,
        )
    return codes
