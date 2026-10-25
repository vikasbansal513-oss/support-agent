from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.contracts import PolicyV1
from app.v1.policy import PolicyV2
from tests.auth.helpers import MemoryMailer, error_code
from tests.policy.test_api import owner

Factory = async_sessionmaker[AsyncSession]


def v2(**autonomy: str) -> dict[str, Any]:
    body = PolicyV2().model_dump(mode="json")
    body["autonomy"] = {**body["autonomy"], **autonomy}
    body["enabled_tools"] = sorted({*body["enabled_tools"], *autonomy})
    return body


async def test_owner_can_draft_v2_with_approval_level(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    created = await client.post(
        "/policies", json={"content": v2(order_cancel="approval")}, headers=h
    )
    assert created.status_code == 201
    assert created.json()["content"]["schema_version"] == 2
    assert created.json()["content"]["autonomy"]["order_cancel"] == "approval"


async def test_owner_cannot_raise_a_tool_to_auto(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    blocked = await client.post("/policies", json={"content": v2(refund_create="auto")}, headers=h)
    assert blocked.status_code == 422 and error_code(blocked) == "E-POL-002"
    ok = await client.post("/policies", json={"content": v2()}, headers=h)
    put = await client.put(
        f"/policies/{ok.json()['id']}", json={"content": v2(order_cancel="auto")}, headers=h
    )
    assert error_code(put) == "E-POL-002"


async def test_v2_publish_then_no_downgrade_to_v1(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    first = (await client.post("/policies", json={"content": v2()}, headers=h)).json()
    pub = await client.post(f"/policies/{first['id']}/publish", headers=h)
    assert pub.status_code == 200 and pub.json()["content"]["schema_version"] == 2
    down = await client.post(
        "/policies", json={"content": PolicyV1().model_dump(mode="json")}, headers=h
    )
    assert down.status_code == 422 and error_code(down) == "E-POL-002"
    listed = (await client.get("/policies", headers=h)).json()
    assert [p["status"] for p in listed] == ["published"]


async def test_v1_policy_still_works(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    h = await owner(client, mailer, runtime_factory)
    created = await client.post(
        "/policies", json={"content": PolicyV1().model_dump(mode="json")}, headers=h
    )
    assert created.status_code == 201 and created.json()["content"].get("schema_version") != 2
