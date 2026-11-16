import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.integrations.http import DbCallRecorder, ExternalHttp
from app.speech.providers import make_stt, make_tts
from app.speech.runtime import load_effective
from app.voice import service
from app.voice.media_stream import EdesyStreamSession, OutMessage

logger = logging.getLogger("app.webhooks.voice_stream")
router = APIRouter(tags=["webhooks"])
Factory = async_sessionmaker[AsyncSession]
MAX_FRAME_BYTES = 65_536


async def _send(websocket: WebSocket, out: OutMessage) -> bool:
    """Returns False if the session should close."""
    if out.kind == "audio":
        await websocket.send_bytes(out.data)
    elif out.kind == "control":
        await websocket.send_text(json.dumps(out.data))
    elif out.kind == "close":
        return False
    return True


@router.websocket("/webhooks/voice-stream/{token}")
async def voice_stream_webhook(
    websocket: WebSocket,
    token: str,
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> None:
    voice = await service.resolve_token(factory, token)
    if voice is None or voice.provider != "edesy":
        await websocket.close(code=4404)
        return
    speech_settings = await load_effective(factory, get_settings().encryption_key)
    if speech_settings is None or not speech_settings.api_key:
        logger.error("edesy media stream: no platform speech settings configured")
        await websocket.close(code=4500)
        return
    await websocket.accept()
    http_stt = ExternalHttp(
        f"speech_{speech_settings.stt_provider}_stt",
        recorder=DbCallRecorder(factory, voice.tenant_id),
    )
    http_tts = ExternalHttp(
        f"speech_{speech_settings.tts_provider}_tts",
        recorder=DbCallRecorder(factory, voice.tenant_id),
    )
    stt = make_stt(speech_settings.stt_provider, http_stt, speech_settings.api_key)
    tts = make_tts(speech_settings.tts_provider, http_tts, speech_settings.api_key)
    session = EdesyStreamSession(factory, voice, stt, tts, speech_settings.sample_rate_hz)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text_body = message.get("text")
            bytes_body = message.get("bytes")
            if text_body is not None:
                outs = await _handle_text(session, text_body)
            elif bytes_body is not None:
                if len(bytes_body) > MAX_FRAME_BYTES:
                    continue
                outs = await session.handle_audio_chunk(bytes_body)
            else:
                continue
            keep_going = True
            for out in outs:
                keep_going = await _send(websocket, out) and keep_going
            if not keep_going:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("voice media stream failed")
    finally:
        await session.handle_disconnect()
        try:
            await websocket.close()
        except RuntimeError:
            pass  # already closed


async def _handle_text(session: EdesyStreamSession, raw: str) -> list[OutMessage]:
    # TEMPORARY DIAGNOSTIC (Nov 2026): Edesy's Media Streams metadata frame shape was
    # never confirmed against a real account - calls connect and stream audio in, but
    # no reply goes out, with no error logged anywhere else in this path. Logging the
    # raw text frame here is the only way to see what Edesy actually sends vs. what
    # app.voice.media_stream.EdesyStreamSession.handle_metadata expects
    # (callSid/call_sid/callId + from + to). Remove once the real shape is confirmed
    # and the parsing below (and in handle_metadata) is adjusted to match it.
    logger.info("voice-stream text frame received: %s", raw[:2000])
    try:
        frame = json.loads(raw)
    except ValueError:
        logger.warning("voice-stream text frame was not valid JSON")
        return []
    if not isinstance(frame, dict):
        logger.warning("voice-stream text frame was valid JSON but not an object: %r", type(frame))
        return []
    # First (and only) text frame per Edesy's documented protocol is the metadata
    # frame (callSid/from/to/direction/audio_format). Any later text frame we don't
    # recognise is ignored rather than erroring the call.
    if "from" in frame or "callSid" in frame or "call_sid" in frame:
        return await session.handle_metadata(frame)
    logger.warning(
        "voice-stream text frame had none of from/callSid/call_sid keys: %s", sorted(frame.keys())
    )
    return []
