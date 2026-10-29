import uuid

from sqlalchemy import text

from app.auth.tokens import create_access_token
from app.core.db import session_scope
from tests.agent.helpers import AgentEnv
from tests.auth.helpers import error_code
from tests.voice.helpers import configure


async def staff_headers(env: AgentEnv) -> dict[str, str]:
    user_id = uuid.uuid4()
    async with session_scope(env.factory, tenant_id=env.wa.tenant_id) as session:
        await session.execute(
            text("INSERT INTO users (id, email, name) VALUES (:i, :e, 'Agent One')"),
            {"i": user_id, "e": f"agent-{user_id.hex[:8]}@example.com"},
        )
        await session.execute(
            text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'agent')"),
            {"t": env.wa.tenant_id, "u": user_id},
        )
    token, _ = create_access_token(
        user_id=user_id, realm="brand", role="agent", tenant_id=env.wa.tenant_id, mfa_ok=True
    )
    return {"Authorization": f"Bearer {token}"}


async def test_owner_sets_public_number_and_gets_forwarding_code(env: AgentEnv) -> None:
    voice = await configure(env)
    out = await env.client.put(
        "/voice/public-number",
        json={"client_public_number": "9876543210", "carrier": "jio"},
        headers=env.wa.headers,
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["client_public_number"] == "9876543210"
    assert body["carrier"] == "jio"
    assert body["forwarding_confirmed"] is False
    assert body["forwarding_code"] == f"*21*{voice.called.lstrip('+')}#"
    assert body["forwarding_off_code"] == "#21#"
    # called_number (the AI's own number) must be untouched by this route
    assert body["called_number"] == voice.called


async def test_owner_confirms_forwarding(env: AgentEnv) -> None:
    await configure(env)
    await env.client.put(
        "/voice/public-number",
        json={"client_public_number": "9876543210", "carrier": "airtel"},
        headers=env.wa.headers,
    )
    confirmed = await env.client.put(
        "/voice/public-number/confirm", json={"forwarding_confirmed": True}, headers=env.wa.headers
    )
    assert confirmed.status_code == 200 and confirmed.json()["forwarding_confirmed"] is True


async def test_staff_role_cannot_set_public_number(env: AgentEnv) -> None:
    await configure(env)
    staff = await staff_headers(env)
    out = await env.client.put(
        "/voice/public-number",
        json={"client_public_number": "9876543210", "carrier": "jio"},
        headers=staff,
    )
    assert out.status_code == 403


async def test_bad_format_number_is_rejected(env: AgentEnv) -> None:
    await configure(env)
    out = await env.client.put(
        "/voice/public-number",
        json={"client_public_number": "12345", "carrier": "jio"},
        headers=env.wa.headers,
    )
    assert out.status_code == 422


async def test_clearing_the_number_resets_carrier_and_confirmation(env: AgentEnv) -> None:
    await configure(env)
    await env.client.put(
        "/voice/public-number",
        json={"client_public_number": "9876543210", "carrier": "jio"},
        headers=env.wa.headers,
    )
    await env.client.put(
        "/voice/public-number/confirm", json={"forwarding_confirmed": True}, headers=env.wa.headers
    )
    cleared = await env.client.put(
        "/voice/public-number",
        json={"client_public_number": None, "carrier": None},
        headers=env.wa.headers,
    )
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["client_public_number"] is None
    assert body["forwarding_confirmed"] is False


async def test_public_number_needs_ai_number_configured_first(env: AgentEnv) -> None:
    out = await env.client.put(
        "/voice/public-number",
        json={"client_public_number": "9876543210", "carrier": "jio"},
        headers=env.wa.headers,
    )
    assert out.status_code == 404 and error_code(out) == "E-NF-001"
