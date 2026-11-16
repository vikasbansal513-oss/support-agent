from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.speech.runtime import EffectiveSpeech, encrypt_key, load_effective, validate_choice

router = APIRouter(prefix="/admin/speech-settings", tags=["admin-speech-settings"])
Factory = async_sessionmaker[AsyncSession]
ProviderName = Literal["google", "sarvam"]


class SpeechSettingsOut(BaseModel):
    stt_provider: ProviderName = "google"
    tts_provider: ProviderName = "google"
    sample_rate_hz: Literal[8000, 16000] = 16000
    has_key: bool = False
    configured: bool = False
    updated_at: datetime | None = None


class SpeechSettingsIn(BaseModel):
    stt_provider: ProviderName
    tts_provider: ProviderName
    sample_rate_hz: Literal[8000, 16000] = 16000
    api_key: str | None = Field(default=None, min_length=8, max_length=500)
    reason: str = Field(min_length=5, max_length=300)


def _out(current: EffectiveSpeech | None, updated_at: datetime | None) -> SpeechSettingsOut:
    if current is None:
        return SpeechSettingsOut(configured=False, updated_at=updated_at)
    return SpeechSettingsOut(
        stt_provider=current.stt_provider,
        tts_provider=current.tts_provider,
        sample_rate_hz=current.sample_rate_hz,
        has_key=bool(current.api_key),
        configured=True,
        updated_at=updated_at,
    )


async def _stored(session: AsyncSession) -> tuple[str, str | None, datetime] | None:
    row = (
        await session.execute(
            text(
                "SELECT stt_provider, api_key_enc, updated_at FROM platform_speech_settings"
                " WHERE id = 1"
            )
        )
    ).first()
    return (row.stt_provider, row.api_key_enc, row.updated_at) if row else None


@router.get("")
async def read_settings(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechSettingsOut:
    enc_key = get_settings().encryption_key
    current = await load_effective(factory, enc_key)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        stored = await _stored(session)
    return _out(current, stored[2] if stored else None)


@router.put("")
async def save_settings(
    body: SpeechSettingsIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechSettingsOut:
    problem = validate_choice(body.stt_provider, body.tts_provider, body.sample_rate_hz)
    if problem:
        raise AppError("E-VAL-001", detail=problem)
    enc_key = get_settings().encryption_key
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        stored = await _stored(session)
        key_enc: str | None
        if body.api_key:
            key_enc = encrypt_key(body.api_key, enc_key)
        elif stored and stored[0] == body.stt_provider and stored[1]:
            key_enc = stored[1]
        else:
            raise AppError("E-VAL-001", detail="api_key is required")
        row = (
            await session.execute(
                text(
                    "INSERT INTO platform_speech_settings"
                    " (id, stt_provider, tts_provider, sample_rate_hz, api_key_enc,"
                    " updated_by, updated_at)"
                    " VALUES (1, :stt, :tts, :rate, :k, :u, now())"
                    " ON CONFLICT (id) DO UPDATE SET stt_provider = :stt, tts_provider = :tts,"
                    " sample_rate_hz = :rate, api_key_enc = :k, updated_by = :u, updated_at = now()"
                    " RETURNING updated_at"
                ),
                {
                    "stt": body.stt_provider,
                    "tts": body.tts_provider,
                    "rate": body.sample_rate_hz,
                    "k": key_enc,
                    "u": admin.user_id,
                },
            )
        ).one()
        await record(
            session,
            AuditEvent(
                action="admin.speech_settings_changed",
                entity_type="platform",
                entity_id="speech_settings",
                old_value={"stt_provider": stored[0]} if stored else {},
                new_value={
                    "stt_provider": body.stt_provider,
                    "tts_provider": body.tts_provider,
                    "sample_rate_hz": body.sample_rate_hz,
                    "key_updated": bool(body.api_key),
                },
                reason=body.reason,
                actor_user_id=admin.user_id,
                actor_role="admin",
            ),
        )
    current = await load_effective(factory, enc_key)
    return _out(current, row.updated_at)
