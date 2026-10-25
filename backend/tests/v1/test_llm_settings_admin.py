from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text

from app.agent import runner
from app.core.db import session_scope
from app.llm.provider import FakeProvider, OpenAICompatProvider, ProviderError
from app.llm.runtime import load_effective
from tests.agent.helpers import AgentEnv
from tests.v1.test_autonomy_admin import admin_headers

URL = "/admin/llm-settings"


async def sq(env: AgentEnv, sql: str) -> list[Any]:
    async with session_scope(env.factory, super_admin=True) as session:
        return list((await session.execute(text(sql))).all())


async def wipe(env: AgentEnv) -> None:
    async with session_scope(env.factory, super_admin=True) as session:
        await session.execute(text("DELETE FROM platform_llm_settings"))


@pytest.fixture(autouse=True)
async def clean_settings(env: AgentEnv, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setattr(runner.overrides, "llm", None)
    await wipe(env)
    yield
    await wipe(env)


GROQ = {
    "provider": "groq",
    "model": "llama-3.3-70b-versatile",
    "api_key": "gsk_test_key_123456",
    "reason": "groq par shift kar rahe",
}


async def test_defaults_come_from_deploy_settings(env: AgentEnv) -> None:
    admin = await admin_headers(env)
    out = await env.client.get(URL, headers=admin)
    assert out.status_code == 200
    body = out.json()
    assert body["source"] == "deploy" and body["provider"] == "anthropic"
    assert body["updated_at"] is None


async def test_admin_saves_provider_key_is_encrypted_audited_and_used(env: AgentEnv) -> None:
    admin = await admin_headers(env)
    saved = await env.client.put(URL, json=GROQ, headers=admin)
    assert saved.status_code == 200
    body = saved.json()
    assert body["source"] == "dashboard" and body["provider"] == "groq" and body["has_key"] is True
    assert "gsk_test_key_123456" not in saved.text
    shown = await env.client.get(URL, headers=admin)
    assert shown.json()["model"] == "llama-3.3-70b-versatile"
    assert "gsk_test_key_123456" not in shown.text

    stored = await sq(env, "SELECT api_key_enc FROM platform_llm_settings")
    assert stored[0].api_key_enc and "gsk_test_key_123456" not in stored[0].api_key_enc
    audit = await sq(
        env, "SELECT new_value, reason FROM audit_log WHERE action = 'admin.llm_settings_changed'"
    )
    assert len(audit) == 1 and audit[0].reason == GROQ["reason"]
    assert "gsk_test_key_123456" not in str(audit[0].new_value)

    effective = await load_effective(env.factory)
    assert (effective.provider, effective.api_key, effective.source) == (
        "groq",
        "gsk_test_key_123456",
        "dashboard",
    )
    client = await runner._llm(env.factory, env.wa.tenant_id, None)
    assert isinstance(client._provider, OpenAICompatProvider)
    assert client._provider.model == "llama-3.3-70b-versatile"


async def test_model_change_keeps_stored_key(env: AgentEnv) -> None:
    admin = await admin_headers(env)
    await env.client.put(URL, json=GROQ, headers=admin)
    again = await env.client.put(
        URL,
        json={"provider": "groq", "model": "llama-3.1-8b-instant", "reason": "chhota model try"},
        headers=admin,
    )
    assert again.status_code == 200 and again.json()["has_key"] is True
    effective = await load_effective(env.factory)
    assert effective.model == "llama-3.1-8b-instant" and effective.api_key == "gsk_test_key_123456"


@pytest.mark.parametrize(
    "change",
    [
        {"api_key": None},
        {"model": "claude-haiku-5-5", "api_key": "gsk_test_key_123456"},
        {"provider": "openai_compatible", "base_url": ""},
        {"provider": "openai_compatible", "base_url": "http://example.com/v1"},
        {"provider": "openai_compatible", "base_url": "https://127.0.0.1/v1"},
        {"provider": "openai_compatible", "base_url": "https://localhost/v1"},
        {"reason": "no"},
    ],
)
async def test_invalid_choices_are_rejected(env: AgentEnv, change: dict[str, str | None]) -> None:
    admin = await admin_headers(env)
    body = {**GROQ, **change}
    body = {k: v for k, v in body.items() if v is not None}
    out = await env.client.put(URL, json=body, headers=admin)
    assert out.status_code == 422
    assert await sq(env, "SELECT 1 FROM platform_llm_settings") == []


async def test_owner_cannot_read_or_change(env: AgentEnv) -> None:
    assert (await env.client.get(URL, headers=env.wa.headers)).status_code == 403
    assert (await env.client.put(URL, json=GROQ, headers=env.wa.headers)).status_code == 403
    assert (
        await env.client.post(f"{URL}/test", json=GROQ, headers=env.wa.headers)
    ).status_code == 403


async def test_test_button_reports_ok_and_errors(
    env: AgentEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await admin_headers(env)
    fakes = [FakeProvider(responses=["ok"]), FakeProvider(responses=[ProviderError("auth_error")])]
    monkeypatch.setattr(
        "app.api.admin_llm_settings.make_provider", lambda *args, **kwargs: fakes.pop(0)
    )
    good = await env.client.post(f"{URL}/test", json=GROQ, headers=admin)
    assert good.status_code == 200 and good.json()["ok"] is True
    bad = await env.client.post(f"{URL}/test", json=GROQ, headers=admin)
    assert bad.json()["ok"] is False and bad.json()["error"] == "auth_error"
    assert await sq(env, "SELECT 1 FROM platform_llm_settings") == []
