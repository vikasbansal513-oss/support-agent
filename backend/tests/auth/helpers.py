import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import pyotp
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope

PASSWORD = "correct-horse-battery"
ORIGIN = "https://app.example.test"
Factory = async_sessionmaker[AsyncSession]


@dataclass
class MemoryMailer:
    sent: list[tuple[str, str, str]] = field(default_factory=list)

    async def send(self, to: str, subject: str, text: str) -> None:
        self.sent.append((to, subject, text))

    def last_token(self, to: str) -> str:
        for recipient, _, body in reversed(self.sent):
            if recipient == to:
                found = re.search(r"token=([\w-]+)", body)
                assert found, body
                return found.group(1)
        raise AssertionError(f"no mail for {to}")

    def count(self, to: str) -> int:
        return sum(1 for recipient, _, _ in self.sent if recipient == to)


@dataclass
class Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.com"


def new_ip() -> str:
    octets = uuid.uuid4().bytes
    return f"10.{octets[0]}.{octets[1]}.{octets[2]}"


async def signup(
    client: httpx.AsyncClient, email: str, brand: str = "Test Brand", password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        "/auth/signup",
        json={"brand_name": brand, "name": "Owner One", "email": email, "password": password},
    )


async def set_tenant_status(factory: Factory, email: str, status: str) -> uuid.UUID:
    async with session_scope(factory, super_admin=True) as session:
        row = (
            await session.execute(
                text(
                    "UPDATE tenants SET status = :s WHERE id = "
                    "(SELECT m.tenant_id FROM memberships m "
                    "JOIN users u ON u.id = m.user_id WHERE u.email = :e) RETURNING id"
                ),
                {"s": status, "e": email},
            )
        ).scalar_one()
    return uuid.UUID(str(row))


async def active_user(
    client: httpx.AsyncClient, mailer: MemoryMailer, factory: Factory, email: str | None = None
) -> str:
    email = email or new_email()
    response = await signup(client, email)
    assert response.status_code == 202, response.text
    verify = await client.post("/auth/verify-email", json={"token": mailer.last_token(email)})
    assert verify.status_code == 200, verify.text
    await set_tenant_status(factory, email, "active")
    return email


async def login(
    client: httpx.AsyncClient, email: str, password: str = PASSWORD, admin: bool = False
) -> httpx.Response:
    path = "/admin/auth/login" if admin else "/auth/login"
    return await client.post(path, json={"email": email, "password": password})


def bearer(response: httpx.Response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def refresh_headers(
    response: httpx.Response, extra: dict[str, str] | None = None
) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    headers.update(extra or {})
    return headers


def totp_now(secret: str, offset_steps: int = 0) -> str:
    import time

    return pyotp.TOTP(secret).at(int(time.time()) + offset_steps * 30)


async def make_admin(factory: Factory, email: str | None = None) -> str:
    from scripts.seed_admin import seed_admin

    email = email or f"admin-{uuid.uuid4().hex[:8]}@example.com"
    await seed_admin(factory, email, "Ops Admin", PASSWORD)
    return email


def error_code(response: httpx.Response) -> str:
    body: dict[str, Any] = response.json()
    return str(body["code"])
