import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.integrations import service


async def connect_gmail(
    factory: async_sessionmaker[AsyncSession], tenant_slug: str, address: str, refresh_token: str
) -> None:
    address = address.strip().lower()
    if "@" not in address or len(refresh_token) < 10:
        raise AppError("E-VAL-001", detail="address or refresh token")
    async with session_scope(factory, super_admin=True) as session:
        tenant_id = (
            await session.execute(
                text("SELECT id FROM tenants WHERE slug = :s"), {"s": tenant_slug}
            )
        ).scalar_one_or_none()
    if tenant_id is None:
        raise AppError("E-NF-001")
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await service.store_secret(
            session,
            "gmail",
            {"address": address},
            {"refresh_token": refresh_token},
            refresh_token,
        )
        await record(
            session,
            AuditEvent(
                action="integration.set",
                entity_type="integration",
                entity_id="gmail",
                actor_role="operator_script",
            ),
        )


def main() -> int:
    slug = os.environ.get("TENANT_SLUG", "")
    address = os.environ.get("GMAIL_ADDRESS", "")
    token = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    if not (slug and address and token):
        print("TENANT_SLUG, GMAIL_ADDRESS and GMAIL_REFRESH_TOKEN are required", file=sys.stderr)
        return 2
    asyncio.run(connect_gmail(get_session_factory(), slug, address, token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
