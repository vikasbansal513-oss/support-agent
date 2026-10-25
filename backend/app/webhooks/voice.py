import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_session_factory
from app.core.errors import AppError, new_error_id
from app.voice import providers, service, speech
from app.voice.contracts import VoiceReplyOut

logger = logging.getLogger("app.webhooks.voice")
router = APIRouter(tags=["webhooks"])
Factory = async_sessionmaker[AsyncSession]
MAX_BODY_BYTES = 16_384


def _fallback(provider: str = "generic", action: str = "hangup") -> JSONResponse:
    reply = VoiceReplyOut(say=speech.voice_line("error", "hinglish"), action=action)
    return JSONResponse(providers.get_adapter(provider).render(reply))


@router.post("/webhooks/voice/{token}")
async def voice_webhook(
    token: str,
    request: Request,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> JSONResponse:
    voice = await service.resolve_token(factory, token)
    if voice is None:
        raise AppError("E-WEB-001", status_code=404)
    adapter = providers.get_adapter(voice.provider)
    try:
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return _fallback(voice.provider)
        body: Any = json.loads(raw)
        if not isinstance(body, dict):
            return _fallback(voice.provider)
        event = adapter.parse(body)
    except (ValueError, ValidationError):
        return _fallback(voice.provider)
    if event.to_number != voice.called_number:
        return _fallback(voice.provider)
    try:
        reply = await service.handle(factory, voice, event)
    except Exception as exc:
        logger.error(
            "voice webhook failed",
            extra={"error_id": new_error_id(), "error_class": type(exc).__name__},
        )
        return _fallback(voice.provider)
    return JSONResponse(adapter.render(reply))
