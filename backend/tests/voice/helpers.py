import secrets
from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import get_settings
from tests.agent.helpers import AgentEnv

CALLER = "+919876543210"
CALLED = "+911140000000"
TRANSFER = "+919811122233"


@pytest.fixture(autouse=True)
def _no_anomaly_stop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ANOMALY_AUTO_THRESHOLD", "1000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Voice:
    def __init__(self, env: AgentEnv, path: str, called: str = CALLED) -> None:
        self.env, self.path, self.called = env, path, called
        self.call_id = "CA-1"

    async def send(self, event: str, **fields: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "event": event,
            "call_id": self.call_id,
            "from": CALLER,
            "to": self.called,
            **fields,
        }
        resp = await self.env.client.post(self.path, json=body)
        assert resp.status_code == 200, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def start(self) -> dict[str, Any]:
        return await self.send("start")

    async def say(
        self, speech: str, llm: str | None = None, confidence: float | None = 0.95, **extra: Any
    ) -> dict[str, Any]:
        if llm is not None:
            self.env.provider.responses = [llm]
        fields: dict[str, Any] = {"speech": speech, **extra}
        if confidence is not None:
            fields["confidence"] = confidence
        return await self.send("turn", **fields)


async def configure(
    env: AgentEnv, transfer: str | None = None, enabled: bool = True, provider: str = "generic"
) -> Voice:
    called = "+9111" + "".join(str(secrets.randbelow(10)) for _ in range(8))
    body: dict[str, Any] = {"called_number": called, "enabled": enabled, "provider": provider}
    if transfer:
        body["transfer_number"] = transfer
    resp = await env.client.put("/voice/settings", json=body, headers=env.wa.headers)
    assert resp.status_code == 200, resp.text
    url = resp.json()["webhook_url"]
    return Voice(env, url[url.index("/webhooks/voice/") :], called)
