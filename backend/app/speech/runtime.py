import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import Crypto, CryptoError
from app.core.db import session_scope

logger = logging.getLogger("app.speech.runtime")
Factory = async_sessionmaker[AsyncSession]

DEFAULT_SAMPLE_RATE_HZ = 16000


@dataclass(frozen=True)
class EffectiveSpeech:
    stt_provider: str
    tts_provider: str
    api_key: str
    # 2026-11-18: STT and TTS can now be different providers (e.g. stt_provider=
    # "sarvam", tts_provider="google") that need different API keys - api_key above
    # is kept as the TTS key (unchanged name/meaning for backward compatibility with
    # existing callers that only ever read .api_key for the single-provider case).
    # stt_api_key is the STT-specific key; load_effective() below falls back to
    # api_key when no separate STT key has been saved, so nothing changes for an
    # existing row/admin that never sets one.
    stt_api_key: str
    sample_rate_hz: int
    source: str


def validate_choice(stt_provider: str, tts_provider: str, sample_rate_hz: int) -> str | None:
    from app.speech.providers import STT_PROVIDERS, TTS_PROVIDERS

    if stt_provider not in STT_PROVIDERS:
        return "unknown stt_provider"
    if tts_provider not in TTS_PROVIDERS:
        return "unknown tts_provider"
    if sample_rate_hz not in (8000, 16000):
        return "sample_rate_hz must be 8000 or 16000"
    return None


def encrypt_key(api_key: str, encryption_key: str) -> str:
    return Crypto(encryption_key).encrypt(api_key)


async def load_effective(factory: Factory, encryption_key: str) -> EffectiveSpeech | None:
    """None means no admin choice saved yet - the Edesy Media Streams endpoint should
    refuse the call rather than guess a provider, same as how voice calls refuse when
    telephony is misconfigured elsewhere in app.voice.service.
    """
    try:
        async with session_scope(factory, super_admin=True) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT stt_provider, tts_provider, sample_rate_hz, api_key_enc,"
                        " stt_api_key_enc FROM platform_speech_settings WHERE id = 1"
                    )
                )
            ).first()
    except SQLAlchemyError as exc:
        logger.error("speech settings read failed", extra={"error_class": type(exc).__name__})
        return None
    if row is None:
        return None
    api_key = ""
    if row.api_key_enc:
        try:
            api_key = Crypto(encryption_key).decrypt(row.api_key_enc)
        except CryptoError:
            logger.error("speech settings key decrypt failed")
    # 2026-11-18: stt_api_key_enc is a new, nullable column (migration 0041) - every
    # row saved before this change, and any row saved since without the admin
    # explicitly setting a separate STT key, has it NULL. Falling back to the
    # already-decrypted api_key in that case keeps STT and TTS sharing one key
    # exactly as before - only a dashboard save that sets a distinct STT key changes
    # this.
    stt_api_key = api_key
    if row.stt_api_key_enc:
        try:
            stt_api_key = Crypto(encryption_key).decrypt(row.stt_api_key_enc)
        except CryptoError:
            logger.error("speech settings stt key decrypt failed")
    return EffectiveSpeech(
        row.stt_provider, row.tts_provider, api_key, stt_api_key, row.sample_rate_hz, "dashboard"
    )
