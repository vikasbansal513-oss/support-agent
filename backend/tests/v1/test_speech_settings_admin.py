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


async def test_sarvam_stt_with_separate_key_keeps_google_tts_key_untouched(
    env: AgentEnv,
) -> None:
    # 2026-11-18: stt_provider and tts_provider can now be different providers with
    # different accounts/keys (migration 0041) - Sarvam STT + Google TTS is the
    # concrete case this was built for.
    from app.core.config import get_settings

    admin = await admin_headers(env)
    await env.client.put(URL, json=GOOGLE, headers=admin)  # google for both, first
    mixed = await env.client.put(
        URL,
        json={
            "stt_provider": "sarvam",
            "tts_provider": "google",
            "sample_rate_hz": 16000,
            "stt_api_key": "sarvam-test-key-98765",
            "reason": "stt ko sarvam pe shift kar rahe, tts google hi rahega",
        },
        headers=admin,
    )
    assert mixed.status_code == 200
    body = mixed.json()
    assert body["stt_provider"] == "sarvam" and body["tts_provider"] == "google"
    assert body["has_key"] is True and body["has_stt_key"] is True

    stored = await sq(env, "SELECT api_key_enc, stt_api_key_enc FROM platform_speech_settings")
    assert stored[0].api_key_enc and stored[0].stt_api_key_enc
    assert stored[0].api_key_enc != stored[0].stt_api_key_enc

    effective = await load_effective(env.factory, get_settings().encryption_key)
    assert effective is not None
    assert effective.api_key == "AIzaSyTestKey123456"  # unchanged TTS/google key
    assert effective.stt_api_key == "sarvam-test-key-98765"


async def test_stt_key_not_set_falls_back_to_shared_key(env: AgentEnv) -> None:
    from app.core.config import get_settings

    admin = await admin_headers(env)
    await env.client.put(URL, json=GOOGLE, headers=admin)
    out = await env.client.get(URL, headers=admin)
    assert out.json()["has_stt_key"] is False

    effective = await load_effective(env.factory, get_settings().encryption_key)
    assert effective is not None
    assert effective.stt_api_key == effective.api_key == "AIzaSyTestKey123456"


async def test_owner_cannot_read_or_change(env: AgentEnv) -> None:
    assert (await env.client.get(URL, headers=env.wa.headers)).status_code == 403
    assert (await env.client.put(URL, json=GOOGLE, headers=env.wa.headers)).status_code == 403
