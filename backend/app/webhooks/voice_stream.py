import asyncio
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

# How long to wait for the *first* frame from the telephony provider after accepting
# the socket. Added Nov 2026 after a real Edesy call connected, stayed open ~25s, and
# disconnected (code 1006) without this app ever receiving a single text or binary
# frame - before this fix there was no logging for that case at all (only the text-
# frame path logged anything), so the call looked silently "stuck" with nothing in the
# logs to explain why. This does not fix the Edesy-side cause (still unconfirmed - see
# the TEMPORARY DIAGNOSTIC logging below); it makes that failure mode loud and
# unambiguous instead of a bare WebSocketDisconnect trace from the cleanup code.
FIRST_FRAME_TIMEOUT_S = 15
# How long to wait between any two frames once streaming has started. Generous (Edesy
# sends audio continuously once started, so a real gap this long means the call is
# effectively dead), kept separate from FIRST_FRAME_TIMEOUT_S in case the two ever need
# different tuning.
INTER_FRAME_TIMEOUT_S = 30


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
    if voice is None:
        # NOTE (Nov 2026): closing before accept() makes uvicorn's websockets
        # implementation reject the HTTP upgrade with a plain 403 to the client,
        # regardless of the close code passed here - that 403 alone gives no reason,
        # so log one explicitly every time this path is hit.
        logger.error("voice-stream: token did not resolve to any voice_numbers row")
        await websocket.close(code=4404)
        return
    if voice.provider != "edesy":
        logger.error(
            "voice-stream: token resolved to tenant=%s but provider=%r (expected 'edesy')",
            voice.tenant_id,
            voice.provider,
        )
        await websocket.close(code=4404)
        return
    speech_settings = await load_effective(factory, get_settings().encryption_key)
    if speech_settings is None or not speech_settings.api_key:
        logger.error("edesy media stream: no platform speech settings configured")
        await websocket.close(code=4500)
        return
    await websocket.accept()
    logger.info("voice-stream accepted: tenant=%s called=%s", voice.tenant_id, voice.called_number)
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
    frame_count = 0
    try:
        while True:
            timeout = FIRST_FRAME_TIMEOUT_S if frame_count == 0 else INTER_FRAME_TIMEOUT_S
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=timeout)
            except TimeoutError:
                if frame_count == 0:
                    logger.error(
                        "voice-stream: no frame received from provider within %ss of "
                        "accept (tenant=%s) - connection was open but silent; nothing "
                        "was ever sent to us to process",
                        timeout,
                        voice.tenant_id,
                    )
                else:
                    logger.error(
                        "voice-stream: no frame received for %ss after %d frame(s) "
                        "(tenant=%s) - treating as dead and closing",
                        timeout,
                        frame_count,
                        voice.tenant_id,
                    )
                break
            if message.get("type") == "websocket.disconnect":
                logger.info(
                    "voice-stream: provider sent websocket.disconnect after %d frame(s)",
                    frame_count,
                )
                break
            text_body = message.get("text")
            bytes_body = message.get("bytes")
            if text_body is not None:
                frame_count += 1
                # TEMPORARY DIAGNOSTIC (Nov 2026): Edesy's Media Streams metadata frame
                # shape was never confirmed against a real account. Logging every raw
                # text frame here (not just the first) is the only way to see what
                # Edesy actually sends vs. what app.voice.media_stream.EdesyStreamSession
                # .handle_metadata expects (callSid/call_sid/callId + from + to).
                # Remove once the real shape is confirmed and stable in production.
                logger.info(
                    "voice-stream text frame #%d received: %s", frame_count, text_body[:2000]
                )
                outs = await _handle_text(session, text_body, websocket)
            elif bytes_body is not None:
                frame_count += 1
                if frame_count == 1 or frame_count % 50 == 0:
                    # Not logging every single binary frame (would flood the logs for a
                    # whole call) - just the first one (proof that audio ever arrived at
                    # all) and then a periodic heartbeat.
                    logger.info(
                        "voice-stream binary frame #%d received: %d bytes",
                        frame_count,
                        len(bytes_body),
                    )
                if len(bytes_body) > MAX_FRAME_BYTES:
                    logger.warning(
                        "voice-stream: dropping oversized binary frame #%d (%d bytes > %d limit)",
                        frame_count,
                        len(bytes_body),
                        MAX_FRAME_BYTES,
                    )
                    continue
                outs = await session.handle_audio_chunk(bytes_body)
            else:
                logger.warning(
                    "voice-stream: received message with neither text nor bytes: %r",
                    sorted(message.keys()),
                )
                continue
            keep_going = True
            for out in outs:
                keep_going = await _send(websocket, out) and keep_going
            if not keep_going:
                break
    except WebSocketDisconnect:
        logger.info("voice-stream: WebSocketDisconnect after %d frame(s)", frame_count)
    except Exception:
        logger.exception("voice media stream failed after %d frame(s)", frame_count)
    finally:
        await session.handle_disconnect()
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass  # already closed / already disconnected on the wire


async def _handle_text(
    session: EdesyStreamSession, raw: str, websocket: WebSocket
) -> list[OutMessage]:
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
        # on_early sends the filler audio (see media_stream.FILLER_TEXT) straight to
        # the wire the moment the call is validated, instead of waiting for it to come
        # back in this function's own return value - that return only happens after
        # the slow LLM+TTS round trip for the real reply, which is exactly the delay
        # the filler exists to cover. Errors mid-send still surface normally: _send
        # raises on a closed/broken socket same as it would from the main loop below.
        async def _send_early(batch: list[OutMessage]) -> None:
            for out in batch:
                await _send(websocket, out)

        return await session.handle_metadata(frame, on_early=_send_early)
    logger.warning(
        "voice-stream text frame had none of from/callSid/call_sid keys: %s", sorted(frame.keys())
    )
    return []
