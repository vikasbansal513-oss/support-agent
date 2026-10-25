import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import passwords
from app.core import timeutil
from app.core.db import get_session_factory, session_scope


async def seed_admin(
    factory: async_sessionmaker[AsyncSession], email: str, name: str, password: str
) -> None:
    passwords.validate_new_password(password)
    email = email.strip().lower()
    async with session_scope(factory, super_admin=True) as session:
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO users (email, name, password_hash, email_verified_at, "
                    "password_changed_at) VALUES (:e, :n, :p, :now, :now) "
                    "ON CONFLICT (email) DO UPDATE SET email_verified_at = "
                    "COALESCE(users.email_verified_at, :now) RETURNING id"
                ),
                {
                    "e": email,
                    "n": name.strip(),
                    "p": passwords.hash_password(password),
                    "now": timeutil.utc_now(),
                },
            )
        ).scalar_one()
        await session.execute(
            text("INSERT INTO platform_admins (user_id) VALUES (:u) ON CONFLICT DO NOTHING"),
            {"u": user_id},
        )


def main() -> int:
    email = os.environ.get("ADMIN_EMAIL", "")
    name = os.environ.get("ADMIN_NAME", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not (email and name and password):
        print("ADMIN_EMAIL, ADMIN_NAME and ADMIN_PASSWORD are required", file=sys.stderr)
        return 2
    asyncio.run(seed_admin(get_session_factory(), email, name, password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
