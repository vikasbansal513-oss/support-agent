from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.tokens import sha256_hex
from app.core import timeutil
from app.core.db import session_scope
from app.core.errors import AppError

LIMITS: dict[str, tuple[int, int]] = {
    "signup_ip": (10, 3600),
    "signup_email": (5, 3600),
    "login_ip": (30, 900),
    "login_email": (20, 900),
    "resend_email": (10, 3600),
    "forgot_email": (5, 3600),
    "mfa_ip": (30, 900),
    "mfa_user": (5, 900),
}

_HIT = text(
    "INSERT INTO rate_limits (bucket, window_start, hits) VALUES (:bucket, :window, 1) "
    "ON CONFLICT (bucket, window_start) DO UPDATE SET hits = rate_limits.hits + 1 RETURNING hits"
)


def bucket_name(kind: str, subject: str) -> str:
    return sha256_hex(f"{kind}:{subject.strip().lower()}")


async def hit(factory: async_sessionmaker[AsyncSession], kind: str, subject: str) -> None:
    limit, window_seconds = LIMITS[kind]
    now = int(timeutil.utc_now().timestamp())
    window_start = now - (now % window_seconds)
    async with session_scope(factory, super_admin=True) as session:
        hits = (
            await session.execute(
                _HIT,
                {
                    "bucket": bucket_name(kind, subject),
                    "window": datetime.fromtimestamp(window_start, UTC),
                },
            )
        ).scalar_one()
    if hits > limit:
        raise AppError("E-AUTH-006", detail=f"rate limit {kind}")
