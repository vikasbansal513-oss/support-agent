import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

import pyotp
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import sha256_hex
from app.core import timeutil
from app.core.config import get_settings
from app.core.crypto import Crypto
from app.core.errors import AppError

ISSUER = "Support Agent"
RECOVERY_COUNT = 10
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


@dataclass(frozen=True)
class MfaState:
    confirmed: bool
    recovery_remaining: int


def get_crypto() -> Crypto:
    return Crypto(get_settings().encryption_key)


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret)


def _step(now: datetime) -> int:
    return int(now.timestamp()) // 30


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    return sha256_hex(code.strip().lower().replace(" ", ""))


async def get_state(session: AsyncSession, user_id: uuid.UUID) -> MfaState:
    row = (
        await session.execute(
            text(
                "SELECT confirmed_at IS NOT NULL, COALESCE(cardinality(recovery_hashes), 0) "
                "FROM user_mfa WHERE user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
    if row is None:
        return MfaState(False, 0)
    return MfaState(bool(row[0]), int(row[1]))


async def begin_setup(session: AsyncSession, user_id: uuid.UUID, email: str) -> tuple[str, str]:
    state = await get_state(session, user_id)
    if state.confirmed:
        raise AppError("E-CONF-001", detail="mfa already enabled")
    secret = pyotp.random_base32()
    encrypted = get_crypto().encrypt(secret)
    await session.execute(
        text(
            "INSERT INTO user_mfa (user_id, secret_enc, confirmed_at, last_step, recovery_hashes) "
            "VALUES (:u, :s, NULL, NULL, '{}') "
            "ON CONFLICT (user_id) DO UPDATE SET secret_enc = :s, confirmed_at = NULL, "
            "last_step = NULL, recovery_hashes = '{}', updated_at = now()"
        ),
        {"u": user_id, "s": encrypted},
    )
    uri = _totp(secret).provisioning_uri(name=email, issuer_name=ISSUER)
    return secret, uri


async def _load(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, bool, int | None] | None:
    row = (
        await session.execute(
            text(
                "SELECT secret_enc, confirmed_at IS NOT NULL, last_step "
                "FROM user_mfa WHERE user_id = :u FOR UPDATE"
            ),
            {"u": user_id},
        )
    ).first()
    if row is None:
        return None
    return get_crypto().decrypt(row[0]), bool(row[1]), row[2]


async def _check_totp(
    session: AsyncSession, user_id: uuid.UUID, secret: str, last_step: int | None, code: str
) -> bool:
    if not code.isdigit() or len(code) != 6:
        return False
    now = timeutil.utc_now()
    current = _step(now)
    totp = _totp(secret)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if secrets.compare_digest(totp.at(step * 30), code):
            await session.execute(
                text("UPDATE user_mfa SET last_step = :s, updated_at = now() WHERE user_id = :u"),
                {"s": step, "u": user_id},
            )
            return True
    return False


async def confirm_setup(session: AsyncSession, user_id: uuid.UUID, code: str) -> list[str]:
    loaded = await _load(session, user_id)
    if loaded is None:
        raise AppError("E-AUTH-005", detail="mfa not started")
    secret, confirmed, last_step = loaded
    if confirmed:
        raise AppError("E-CONF-001", detail="mfa already enabled")
    if not await _check_totp(session, user_id, secret, last_step, code):
        raise AppError("E-AUTH-005")
    codes = new_recovery_codes()
    await session.execute(
        text(
            "UPDATE user_mfa SET confirmed_at = :now, recovery_hashes = :h, updated_at = now() "
            "WHERE user_id = :u"
        ),
        {"now": timeutil.utc_now(), "h": [hash_recovery_code(c) for c in codes], "u": user_id},
    )
    return codes


async def regenerate_recovery_codes(
    session: AsyncSession, user_id: uuid.UUID, code: str
) -> list[str]:
    loaded = await _load(session, user_id)
    if loaded is None or not loaded[1]:
        raise AppError("E-AUTH-005", detail="mfa not enabled")
    secret, _, last_step = loaded
    if not await _check_totp(session, user_id, secret, last_step, code):
        raise AppError("E-AUTH-005")
    codes = new_recovery_codes()
    await session.execute(
        text("UPDATE user_mfa SET recovery_hashes = :h, updated_at = now() WHERE user_id = :u"),
        {"h": [hash_recovery_code(c) for c in codes], "u": user_id},
    )
    return codes


async def verify_login_code(session: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    loaded = await _load(session, user_id)
    if loaded is None or not loaded[1]:
        return False
    secret, _, last_step = loaded
    if await _check_totp(session, user_id, secret, last_step, code.strip()):
        return True
    digest = hash_recovery_code(code)
    result = await session.execute(
        text(
            "UPDATE user_mfa SET recovery_hashes = array_remove(recovery_hashes, :h), "
            "updated_at = now() WHERE user_id = :u AND :h = ANY(recovery_hashes)"
        ),
        {"h": digest, "u": user_id},
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]
