import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.senders import Senders, set_override
from app.agent import runner
from app.agent.tools import FakeToolBox
from app.contracts.contracts import OrderFacts, OrderLine, PolicyV1, ToolResult
from app.core.db import session_scope
from app.integrations.gupshup import FakeGupshupSender
from app.jobs import handlers
from app.llm.client import DbCallRecorder as LlmRecorder
from app.llm.client import LlmClient
from app.llm.provider import FakeProvider
from app.worker import run_worker
from tests.auth.helpers import MemoryMailer
from tests.channels.helpers import WaTenant, setup_wa_tenant

Factory = async_sessionmaker[AsyncSession]
FLAGS = {
    k: False
    for k in (
        "asks_if_human",
        "wants_human",
        "mentions_legal",
        "mentions_medical",
        "mentions_fraud",
        "mentions_press",
        "mentions_safety",
    )
}


def cls(intent: str = "order_status", ref: str | None = "#1001", **over: Any) -> str:
    flags = {**FLAGS, **over.pop("flags", {})}
    body = {
        "intent": intent,
        "order_ref": ref,
        "confidence": 0.95,
        "language": "hinglish",
        "sentiment": "normal",
        "flags": flags,
        "mixed_intents": [],
        "summary": "asks status",
        **over,
    }
    return json.dumps(body)


def order(name: str = "#1001") -> ToolResult:
    facts = OrderFacts(
        order_name=name,
        financial_status="paid",
        fulfillment_status="fulfilled",
        lines=[OrderLine(title="Kurta", quantity=2)],
        total_paise=149950,
        created_at=datetime.now(UTC),
        tracking_numbers=[],
        courier=None,
    )
    return ToolResult(tool="order_lookup", ok=True, order=facts)


@dataclass
class AgentEnv:
    wa: WaTenant
    factory: Factory
    owner_factory: Factory
    provider: FakeProvider
    sender: FakeGupshupSender
    tools: FakeToolBox
    mailer: MemoryMailer
    client: httpx.AsyncClient
    extra: dict[str, Any] = field(default_factory=dict)

    async def set_mode(self, mode: str) -> None:
        async with session_scope(self.owner_factory, super_admin=True) as session:
            await session.execute(
                text("UPDATE tenants SET mode = :m WHERE id = :t"),
                {"m": mode, "t": self.wa.tenant_id},
            )

    async def set_paused(self, paused: bool) -> None:
        async with session_scope(self.owner_factory, super_admin=True) as session:
            await session.execute(
                text("UPDATE tenants SET agent_paused = :p WHERE id = :t"),
                {"p": paused, "t": self.wa.tenant_id},
            )

    async def publish_policy(self, **over: Any) -> None:
        body = PolicyV1().model_dump(mode="json")
        body["office_hours"] = {"start": "00:00", "end": "23:59", "days": [0, 1, 2, 3, 4, 5, 6]}
        body.update(over)
        created = await self.client.post(
            "/policies", json={"content": body}, headers=self.wa.headers
        )
        assert created.status_code == 201, created.text
        published = await self.client.post(
            f"/policies/{created.json()['id']}/publish", headers=self.wa.headers
        )
        assert published.status_code == 200, published.text

    async def set_notify_email(self, email: str) -> None:
        async with session_scope(self.factory, tenant_id=self.wa.tenant_id) as session:
            await session.execute(
                text(
                    "INSERT INTO tenant_settings (notify_email_enabled, notify_email)"
                    " VALUES (true, :e) ON CONFLICT (tenant_id) DO UPDATE SET notify_email = :e"
                ),
                {"e": email},
            )

    async def q(self, sql: str, **params: Any) -> list[Any]:
        async with session_scope(self.factory, tenant_id=self.wa.tenant_id) as session:
            return list((await session.execute(text(sql), params)).all())

    async def drain(self) -> dict[str, int]:
        return await run_worker(self.factory, max_seconds=30, idle_sleep=0.01, exit_when_idle=True)

    async def retry_now(self) -> None:
        async with session_scope(self.owner_factory, super_admin=True) as session:
            await session.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'queued'"))

    def senders(self) -> Senders:
        return Senders(whatsapp=self.sender)


async def build_env(
    client: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    owner_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AgentEnv]:
    wa = await setup_wa_tenant(client, mailer, runtime_factory, monkeypatch)
    async with session_scope(owner_factory, super_admin=True) as session:
        await session.execute(text("DELETE FROM jobs WHERE tenant_id <> :t"), {"t": wa.tenant_id})
    provider = FakeProvider()
    sender = FakeGupshupSender()
    tools = FakeToolBox(orders={"#1001": order()})
    env = AgentEnv(wa, runtime_factory, owner_factory, provider, sender, tools, mailer, client)
    runner.overrides.llm = LlmClient(provider, LlmRecorder(runtime_factory))
    runner.overrides.mailer = mailer
    runner.overrides.tools = tools
    runner.overrides.senders = env.senders()
    set_override(env.senders())
    handlers.set_inbound_runner(runner.run_inbound)
    try:
        yield env
    finally:
        handlers.set_inbound_runner(None)
        set_override(None)
        runner.overrides = runner.Overrides()


def new_id() -> uuid.UUID:
    return uuid.uuid4()
