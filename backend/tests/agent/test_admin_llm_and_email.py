import base64
import json

import httpx
from sqlalchemy import text

from app.core.db import session_scope
from app.integrations.gmail import HttpGmailClient
from app.integrations.http import ExternalHttp
from tests.agent.helpers import AgentEnv, cls
from tests.auth.helpers import bearer
from tests.auth.test_admin_flow import _admin_login_with_mfa
from tests.channels.helpers import wa_message


async def test_admin_llm_usage_aggregates_cost_and_latency(env: AgentEnv) -> None:
    async with session_scope(env.owner_factory, super_admin=True) as session:
        await session.execute(
            text(
                "INSERT INTO llm_model_prices VALUES ('fake', 'fake-model', 1.000000, 5.000000)"
                " ON CONFLICT (provider, model) DO UPDATE SET input_per_million = 1, output_per_million = 5"
            )
        )
    await env.publish_policy()
    env.provider.responses = [cls(), cls()]
    for i in range(2):
        await env.client.post(env.wa.path, json=wa_message(f"u{i}", body="order #1001"))
        await env.drain()
    _, verified, _ = await _admin_login_with_mfa(env.client, env.factory)
    rows = (
        await env.client.get(
            f"/admin/llm-usage?tenant_id={env.wa.tenant_id}", headers=bearer(verified)
        )
    ).json()
    assert len(rows) == 1
    assert rows[0]["calls"] == 2 and rows[0]["input_tokens"] == 200
    assert rows[0]["est_cost_micro"] == 2 * (100 * 1 + 20 * 5)
    assert rows[0]["p95_latency_ms"] is not None
    owner = await env.client.get("/admin/llm-usage", headers=env.wa.headers)
    assert owner.status_code in (401, 403)


async def test_gmail_send_reply_builds_threaded_message() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "oauth2" in str(request.url) or "token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"id": "sent-1"})

    http = ExternalHttp("gmail", transport=httpx.MockTransport(handler))
    sent = await HttpGmailClient(http, "refresh").send_reply(
        sender="shop@brand.example",
        to="cust@example.com",
        subject="Order?",
        body="Namaste",
        thread_id="th1",
        in_reply_to="<abc@mail>",
    )
    assert sent == "sent-1"
    payload = json.loads(seen[-1].content)
    assert payload["threadId"] == "th1"
    raw = base64.urlsafe_b64decode(payload["raw"]).decode()
    assert "Subject: Re: Order?" in raw and "In-Reply-To: <abc@mail>" in raw
