import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


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

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        from app.voice.providers import provider_names

        if value not in provider_names():
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


class VoiceSettingsOut(BaseModel):
    configured: bool
    called_number: str | None = None
    transfer_number: str | None = None
    enabled: bool = False
    webhook_url: str | None = None
    provider: str = "generic"
    providers: list[str] = Field(default_factory=list)


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
