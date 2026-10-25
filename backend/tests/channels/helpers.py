import time
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import session_scope
from tests.auth.helpers import MemoryMailer, active_user, bearer, login

Factory = async_sessionmaker[AsyncSession]


class WaTenant:
    def __init__(self, headers: dict[str, str], token: str, tenant_id: uuid.UUID, number: str):
        self.headers = headers
        self.token = token
        self.tenant_id = tenant_id
        self.number = number

    @property
    def path(self) -> str:
        return f"/webhooks/whatsapp/{self.token}"


async def setup_wa_tenant(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    app_name: str = "",
) -> WaTenant:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://api.example.com")
    get_settings.cache_clear()
    email = await active_user(client, mailer, factory)
    headers = bearer(await login(client, email))
    number = "9197" + str(uuid.uuid4().int)[:8]
    put = await client.put(
        "/integrations/gupshup",
        json={
            "userid": "gs-user",
            "password": "gs-pass-wxyz",
            "source_number": number,
            "app_name": app_name,
        },
        headers=headers,
    )
    assert put.status_code == 200, put.text
    token = put.json()["webhook_url"].rsplit("/", 1)[1]
    async with session_scope(factory, super_admin=True) as session:
        tenant_id = (
            await session.execute(
                text("SELECT tenant_id FROM wa_numbers WHERE source_number = :s"), {"s": number}
            )
        ).scalar_one()
    return WaTenant(headers, token, tenant_id, number)


def wa_message(
    message_id: str | None = None,
    phone: str = "919876543210",
    body: str = "Mera order kahan hai #1001",
    app: str = "BrandApp",
    kind: str = "text",
) -> dict[str, Any]:
    return {
        "app": app,
        "timestamp": int(time.time() * 1000),
        "version": 2,
        "type": "message",
        "payload": {
            "id": message_id or f"wamid-{uuid.uuid4().hex[:12]}",
            "source": phone,
            "type": kind,
            "payload": {"text": body} if kind == "text" else {"caption": body},
            "sender": {"phone": phone},
        },
    }
