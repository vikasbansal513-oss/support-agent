import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
INDIAN_MOBILE = re.compile(r"^[6-9][0-9]{9}$")
Carrier = Literal["jio", "airtel", "vi", "bsnl", "other"]


class VoiceEventIn(BaseModel):
    """Provider-neutral call event. A thin adapter per telephony provider maps into this shape."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    event: Literal["start", "turn", "end"]
    call_id: str = Field(min_length=1, max_length=200)
    from_number: str = Field(alias="from")
    to_number: str = Field(alias="to")
    speech: str = Field(default="", max_length=2000)
    digits: str = Field(default="", pattern=r"^[0-9*#]{0,30}$")
    confidence: float | None = Field(default=None, ge=0, le=1)
    turn_id: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=100)
    # 2026-11-17: set only by app.voice.media_stream's Edesy audio path, only on a
    # "turn" event with empty `speech` - means the VAD already confirmed the caller
    # was speaking but STT returned no text, as opposed to true provider-reported
    # silence (no caller audio at all). app.voice.service._turn() uses this to say
    # "I couldn't hear that clearly" instead of "are you still there?", and to hand
    # off as CONFUSED rather than SILENCE_NO_RESPONSE after repeated failures.
    # Defaults False so every other provider (Plivo, generic) and every "start"/"end"
    # event is completely unaffected.
    heard_unclear: bool = False

    @field_validator("from_number", "to_number")
    @classmethod
    def _e164(cls, value: str) -> str:
        cleaned = re.sub(r"[\s\-()]", "", value)
        if not E164.match(cleaned):
            raise ValueError("number must be E.164 (+91...)")
        return cleaned


class VoiceReplyOut(BaseModel):
    say: str
    language: Literal["hi-IN", "en-IN"] = "hi-IN"
    action: Literal["continue", "transfer", "hangup"] = "continue"
    transfer_to: str | None = None
    gather: Literal["speech", "dtmf", "speech_dtmf"] = "speech_dtmf"
    barge_in: bool = True
    briefing: str | None = None


class VoiceSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    called_number: str
    transfer_number: str | None = None
    enabled: bool = True
    rotate_webhook: bool = False
    provider: str = "generic"
    telephony_mode: Literal["ready_made", "diy_plivo", "diy_edesy"] = "ready_made"

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        from app.voice.media_stream import STREAM_PROVIDERS
        from app.voice.providers import provider_names

        if value not in provider_names() and value not in STREAM_PROVIDERS:
            raise ValueError("unknown provider")
        return value

    @field_validator("called_number", "transfer_number")
    @classmethod
    def _e164(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"[\s\-()]", "", value)
        if not E164.match(cleaned):
            raise ValueError("number must be E.164 (+91...)")
        return cleaned

    @model_validator(mode="after")
    def _telephony_mode_provider_match(self) -> "VoiceSettingsIn":
        from app.voice.media_stream import STREAM_PROVIDERS

        if self.telephony_mode == "diy_plivo" and self.provider != "plivo":
            raise ValueError("diy_plivo telephony_mode requires provider='plivo'")
        if self.telephony_mode == "diy_edesy" and self.provider not in STREAM_PROVIDERS:
            raise ValueError("diy_edesy telephony_mode requires provider='edesy'")
        if self.telephony_mode == "ready_made" and (
            self.provider == "plivo" or self.provider in STREAM_PROVIDERS
        ):
            raise ValueError("this provider requires a DIY telephony_mode")
        return self


class VoiceSettingsOut(BaseModel):
    configured: bool
    called_number: str | None = None
    transfer_number: str | None = None
    enabled: bool = False
    webhook_url: str | None = None
    provider: str = "generic"
    telephony_mode: Literal["ready_made", "diy_plivo", "diy_edesy"] = "ready_made"
    providers: list[str] = Field(default_factory=list)
    client_public_number: str | None = None
    carrier: Carrier | None = None
    forwarding_confirmed: bool = False
    forwarding_code: str | None = None
    forwarding_off_code: str | None = None


class PublicNumberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_public_number: str | None = None
    carrier: Carrier | None = None

    @field_validator("client_public_number")
    @classmethod
    def _indian_mobile(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        cleaned = re.sub(r"[\s\-()]", "", value)
        cleaned = re.sub(r"^(\+91|91|0)", "", cleaned)
        if not INDIAN_MOBILE.match(cleaned):
            raise ValueError("number must be a 10-digit Indian mobile number")
        return cleaned


class PublicNumberConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    forwarding_confirmed: bool


class VoiceCallOut(BaseModel):
    id: str
    status: str
    caller_masked: str | None
    turn_count: int
    end_reason: str | None
    started_at: str
    ended_at: str | None
    conversation_id: str
    order_ref: str | None = None
    avg_latency_ms: int = 0
    max_latency_ms: int = 0
    slow_turns: int = 0
    feedback: Literal["good", "bad"] | None = None
    feedback_note: str | None = None
    summary_status: Literal["sent", "skipped", "failed"] | None = None


class VoiceTurnOut(BaseModel):
    at: str
    speaker: Literal["caller", "agent"]
    text: str


class VoiceCallDetail(VoiceCallOut):
    turns: list[VoiceTurnOut]


class VoiceFeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["good", "bad"]
    note: str | None = Field(default=None, max_length=300)


class VoiceStatsOut(BaseModel):
    days: int
    calls: int
    handled_by_agent: int
    transferred: int
    callback: int
    failed: int
    avg_turns: float
    avg_latency_ms: int
    slow_turns: int
    feedback_good: int
    feedback_bad: int
    agent_rate_percent: int
