from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.speech.runtime import load_effective
from tests.agent.helpers import AgentEnv
from tests.v1.test_autonomy_admin import admin_headers

URL = "/admin/speech-settings"


async def sq(env: AgentEnv, sql: str) -> list[Any]:
    async with session_scope(env.factory, super_admin=True) as session:
        return list((await session.execute(text(sql))).all())


async def wipe(env: AgentEnv) -> None:
    async with session_scope(env.factory, super_admin=True) as session:
        await session.execute(text("DELETE FROM platform_speech_settings"))


@pytest.fixture(autouse=True)
async def clean_settings(env: AgentEnv) -> AsyncIterator[None]:
    await wipe(env)
    yield
    await wipe(env)


GOOGLE = {
    "stt_provider": "google",
    "tts_provider": "google",
    "sample_rate_hz": 16000,
    "api_key": "AIzaSyTestKey123456",
    "reason": "edesy media streams ke liye google wire kar rahe",
}


async def test_defaults_are_unconfigured(env: AgentEnv) -> None:
    admin = await admin_headers(env)
    out = await env.client.get(URL, headers=admin)
    assert out.status_code == 200
    body = out.json()
    assert body["configured"] is False and body["has_key"] is False


async def test_admin_saves_key_is_encrypted_audited_and_loadable(env: AgentEnv) -> None:
    from app.core.config import get_settings

    admin = await admin_headers(env)
    saved = await env.client.put(URL, json=GOOGLE, headers=admin)
    assert saved.status_code == 200
    body = saved.json()
    assert body["configured"] is True and body["has_key"] is True
    assert body["stt_provider"] == "google" and body["sample_rate_hz"] == 16000
    assert "AIzaSyTestKey123456" not in saved.text

    stored = await sq(env, "SELECT api_key_enc FROM platform_speech_settings")
    assert stored[0].api_key_enc and "AIzaSyTestKey123456" not in stored[0].api_key_enc
    audit = await sq(
        env,
        "SELECT new_value, reason FROM audit_log WHERE action = 'admin.speech_settings_changed'",
    )
    assert len(audit) == 1 and audit[0].reason == GOOGLE["reason"]
    assert "AIzaSyTestKey123456" not in str(audit[0].new_value)

    effective = await load_effective(env.factory, get_settings().encryption_key)
    assert effective is not None
    assert (effective.stt_provider, effective.api_key, effective.source) == (
        "google",
        "AIzaSyTestKey123456",
        "dashboard",
    )


async def test_sample_rate_change_keeps_stored_key(env: AgentEnv) -> None:
    from app.core.config import get_settings

    admin = await admin_headers(env)
    await env.client.put(URL, json=GOOGLE, headers=admin)
    again = await env.client.put(
        URL,
        json={
            "stt_provider": "google",
            "tts_provider": "google",
            "sample_rate_hz": 8000,
            "reason": "8khz try kar rahe",
        },
        headers=admin,
    )
    assert again.status_code == 200 and again.json()["has_key"] is True
    effective = await load_effective(env.factory, get_settings().encryption_key)
    assert effective is not None
    assert effective.sample_rate_hz == 8000 and effective.api_key == "AIzaSyTestKey123456"


@pytest.mark.parametrize(
    "change",
    [
        {"api_key": None},
        {"stt_provider": "unknown"},
        {"sample_rate_hz": 44100},
        {"reason": "no"},
    ],
)
async def test_invalid_choices_are_rejected(env: AgentEnv, change: dict[str, Any]) -> None:
    admin = await admin_headers(env)
    body = {**GOOGLE, **change}
    body = {k: v for k, v in body.items() if v is not None}
    out = await env.client.put(URL, json=body, headers=admin)
    assert out.status_code in (400, 422)
    assert await sq(env, "SELECT 1 FROM platform_speech_settings") == []


async def test_owner_cannot_read_or_change(env: AgentEnv) -> None:
    assert (await env.client.get(URL, headers=env.wa.headers)).status_code == 403
    assert (await env.client.put(URL, json=GOOGLE, headers=env.wa.headers)).status_code == 403
