import secrets
from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import get_settings
from tests.agent.helpers import AgentEnv
from tests.auth.helpers import bearer
from tests.auth.test_admin_flow import _admin_login_with_mfa

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
    # Calling setup is admin-only now (platform admin dashboard), so tests log in as a
    # platform admin and go through PUT /admin/tenants/{tenant_id}/voice-settings.
    called = "+9111" + "".join(str(secrets.randbelow(10)) for _ in range(8))
    telephony_mode = "diy_plivo" if provider == "plivo" else "ready_made"
    body: dict[str, Any] = {
        "called_number": called,
        "enabled": enabled,
        "provider": provider,
        "telephony_mode": telephony_mode,
    }
    if transfer:
        body["transfer_number"] = transfer
    _, verified, _ = await _admin_login_with_mfa(env.client, env.factory)
    resp = await env.client.put(
        f"/admin/tenants/{env.wa.tenant_id}/voice-settings", json=body, headers=bearer(verified)
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["webhook_url"]
    return Voice(env, url[url.index("/webhooks/voice/") :], called)
