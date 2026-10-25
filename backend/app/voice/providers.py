import re
from typing import Any, Protocol

from app.voice.contracts import VoiceEventIn, VoiceReplyOut


class ProviderPayloadError(ValueError):
    pass


class VoiceProviderAdapter(Protocol):
    name: str

    def parse(self, body: dict[str, Any]) -> VoiceEventIn: ...

    def render(self, reply: VoiceReplyOut) -> dict[str, Any]: ...


class GenericAdapter:
    """Our own neutral JSON. Used when the telephony side can post exactly this shape."""

    name = "generic"

    def parse(self, body: dict[str, Any]) -> VoiceEventIn:
        return VoiceEventIn.model_validate(body)

    def render(self, reply: VoiceReplyOut) -> dict[str, Any]:
        return reply.model_dump(mode="json")


class FakeIvrAdapter:
    """Test double with a different field naming, to prove the mapping layer. Not a real provider."""

    name = "fake_ivr"
    _EVENTS = {"CallStart": "start", "SpeechInput": "turn", "CallEnd": "end"}

    def parse(self, body: dict[str, Any]) -> VoiceEventIn:
        kind = self._EVENTS.get(str(body.get("Type")))
        if kind is None:
            raise ProviderPayloadError("unknown event type")
        confidence = body.get("Confidence")
        return VoiceEventIn.model_validate(
            {
                "event": kind,
                "call_id": body.get("CallSid"),
                "from": body.get("From"),
                "to": body.get("To"),
                "speech": body.get("SpeechResult") or "",
                "digits": re.sub(r"[^0-9*#]", "", str(body.get("Digits") or "")),
                "confidence": float(confidence) if confidence not in (None, "") else None,
                "turn_id": body.get("UtteranceId"),
                "reason": body.get("EndReason"),
            }
        )

    def render(self, reply: VoiceReplyOut) -> dict[str, Any]:
        return {
            "Say": reply.say,
            "Lang": reply.language,
            "Next": {"continue": "Listen", "transfer": "Dial", "hangup": "Hangup"}[reply.action],
            "DialTo": reply.transfer_to,
            "BargeIn": reply.barge_in,
            "Whisper": reply.briefing,
        }


ADAPTERS: dict[str, VoiceProviderAdapter] = {
    GenericAdapter.name: GenericAdapter(),
    FakeIvrAdapter.name: FakeIvrAdapter(),
}


def get_adapter(name: str) -> VoiceProviderAdapter:
    return ADAPTERS.get(name, ADAPTERS["generic"])


def provider_names() -> list[str]:
    return sorted(ADAPTERS)
