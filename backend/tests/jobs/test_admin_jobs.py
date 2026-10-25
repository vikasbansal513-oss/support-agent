import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from app.jobs import queue
from tests.auth.helpers import MemoryMailer, active_user, bearer, error_code, login
from tests.auth.test_admin_flow import _admin_login_with_mfa
from tests.jobs.test_queue import make_tenant

Factory = async_sessionmaker[AsyncSession]


async def admin_headers(client: httpx.AsyncClient, factory: Factory) -> dict[str, str]:
    _, verified, _ = await _admin_login_with_mfa(client, factory)
    return bearer(verified)


async def test_admin_lists_jobs_with_filters_and_paging(
    client: httpx.AsyncClient, runtime_factory: Factory
) -> None:
    tenant = await make_tenant(runtime_factory)
    async with session_scope(runtime_factory, super_admin=True) as session:
        for i in range(5):
            await queue.enqueue(session, tenant, "demo", {"i": i})
        await session.execute(
            text(
                "UPDATE jobs SET status = 'needs_review', error_code = 'BOOM', error_id = 'ERR-9' WHERE tenant_id = :t AND payload->>'i' = '0'"
            ),
            {"t": tenant},
        )
    headers = await admin_headers(client, runtime_factory)
    first = await client.get(f"/admin/jobs?tenant_id={tenant}&limit=2", headers=headers)
    body = first.json()
    assert first.status_code == 200 and len(body["items"]) == 2 and body["next_cursor"]
    second = (
        await client.get(
            f"/admin/jobs?tenant_id={tenant}&limit=10&cursor={body['next_cursor']}", headers=headers
        )
    ).json()
    ids = {i["id"] for i in body["items"]} | {i["id"] for i in second["items"]}
    assert len(ids) == 5 and second["next_cursor"] is None
    review = (
        await client.get(f"/admin/jobs?tenant_id={tenant}&status=needs_review", headers=headers)
    ).json()
    assert [(j["error_code"], j["error_id"]) for j in review["items"]] == [("BOOM", "ERR-9")]
    assert "payload" not in review["items"][0]


async def test_admin_jobs_requires_admin_and_valid_input(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    assert (await client.get("/admin/jobs")).status_code == 401
    email = await active_user(client, mailer, runtime_factory)
    brand = bearer(await login(client, email))
    denied = await client.get("/admin/jobs", headers=brand)
    assert denied.status_code == 403 and error_code(denied) == "E-ADM-001"
    headers = await admin_headers(client, runtime_factory)
    assert (await client.get("/admin/jobs?status=bogus", headers=headers)).status_code == 422
    assert (await client.get("/admin/jobs?cursor=@@", headers=headers)).status_code == 422
