import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.gupshup_parse import hash_token, tokens_equal
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError


@dataclass(frozen=True)
class WaTenant:
    tenant_id: uuid.UUID
    source_number: str
    app_name: str


async def resolve_token(factory: async_sessionmaker[AsyncSession], token: str) -> WaTenant | None:
    digest = hash_token(token)
    async with session_scope(factory) as session:
        row = await _lookup(session, digest)
    if row is None or not tokens_equal(row.webhook_token_hash, digest):
        return None
    return WaTenant(row.tenant_id, row.source_number, row.app_name)


async def _lookup(session: AsyncSession, digest: str) -> Any:
    result = await session.execute(
        text(
            "SELECT tenant_id, source_number, app_name, webhook_token_hash FROM wa_numbers"
            " WHERE webhook_token_hash = :h"
        ),
        {"h": digest},
    )
    return result.first()


def check_ip_allowlist(client_ip: str) -> None:
    raw = get_settings().gupshup_ip_allowlist
    allowed = {entry.strip() for entry in raw.split(",") if entry.strip()}
    if allowed and client_ip not in allowed:
        raise AppError("E-WEB-001")


def request_ip(request: Request) -> str:
    from app.auth.deps import client_ip

    return client_ip(request)
