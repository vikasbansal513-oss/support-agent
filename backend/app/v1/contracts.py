from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from app.contracts.contracts import Language, ReasonCode

Tier = Literal["T0", "T1", "T2", "T3"]
WriteTool = Literal["address_update", "order_cancel", "exchange_request", "refund_create"]
WRITE_TOOLS: tuple[WriteTool, ...] = (
    "address_update",
    "order_cancel",
    "exchange_request",
    "refund_create",
)
Level = Literal["off", "approval", "auto"]
TOOL_TIER: dict[str, Tier] = {
    "order_lookup": "T0",
    "tracking_lookup": "T0",
    "address_update": "T1",
    "order_cancel": "T2",
    "exchange_request": "T2",
    "refund_create": "T3",
}

V1_NEW_REASONS = (
    "ABUSE",
    "THREAT",
    "SENTIMENT_NEGATIVE",
    "CONFUSED",
    "NO_PROGRESS",
    "STEP_LIMIT",
    "CONFIRM_UNCLEAR",
    "POLICY_EXCEPTION",
    "LIMIT_EXCEEDED",
    "HIGH_RISK_FLAG",
    "ACTION_FAILED",
)
ReasonCodeV1 = (
    ReasonCode
    | Literal[
        "ABUSE",
        "THREAT",
        "SENTIMENT_NEGATIVE",
        "CONFUSED",
        "NO_PROGRESS",
        "STEP_LIMIT",
        "CONFIRM_UNCLEAR",
        "POLICY_EXCEPTION",
        "LIMIT_EXCEEDED",
        "HIGH_RISK_FLAG",
        "ACTION_FAILED",
    ]
)
URGENT_REASONS_V1 = frozenset(
    {
        "ANGRY",
        "ABUSE",
        "THREAT",
        "SAFETY",
        "LEGAL",
        "MEDICAL",
        "FRAUD",
        "PRESS",
        "REPEAT_COMPLAINT",
        "SENTIMENT_NEGATIVE",
        "HIGH_RISK_FLAG",
    }
)
HANDOFF_ORDER_V1 = (
    "ANGRY",
    "ABUSE",
    "THREAT",
    "SAFETY",
    "LEGAL",
    "MEDICAL",
    "FRAUD",
    "PRESS",
    "SENTIMENT_NEGATIVE",
    "HIGH_RISK_FLAG",
    "POLICY_EXCEPTION",
    "STEP_LIMIT",
    "ACTION_FAILED",
    "CONFIRM_UNCLEAR",
    "NO_PROGRESS",
    "CONFUSED",
    "REPLY_VALIDATION_FAILED",
)

ActionStatusV1 = Literal[
    "proposed",
    "awaiting_customer",
    "pending_approval",
    "approved",
    "claimed",
    "executed",
    "confirmed",
    "rejected",
    "expired",
    "failed",
    "needs_review",
]
ACTIVE_WRITE_STATUSES = (
    "proposed",
    "awaiting_customer",
    "pending_approval",
    "approved",
    "claimed",
    "executed",
)
LLM_PURPOSES = ("classify", "plan", "reply", "sentiment")

PlanIntent = Literal[
    "order_status",
    "tracking",
    "address_change",
    "cancel",
    "exchange",
    "refund",
    "policy_question",
    "greeting",
    "other",
    "unclear",
]
PlanAction = Literal["answer", "propose", "clarify", "handoff"]
Promise = Literal[
    "refund_initiated",
    "order_cancelled",
    "address_updated",
    "exchange_initiated",
    "human_will_contact",
]


def _clip(limit: int) -> BeforeValidator:
    def clip(value: Any) -> Any:
        if value is None:
            return None
        return (" ".join(str(value).split())[:limit]) or None

    return BeforeValidator(clip)


Short = Annotated[str | None, _clip(200)]


class AddressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line1: str = Field(min_length=3, max_length=120)
    line2: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=2, max_length=60)
    state: str = Field(min_length=2, max_length=60)
    pin: str = Field(pattern=r"^[1-9][0-9]{5}$")
    country: Literal["IN"] = "IN"

    @field_validator("line1", "line2", "city", "state", mode="before")
    @classmethod
    def _squash(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value


class PlanArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")
    order_ref: Short = None
    line_item: Short = None
    new_variant: Short = None
    reason: Short = None
    new_address: AddressIn | None = None

    @field_validator("new_address", mode="before")
    @classmethod
    def _bad_address_is_missing(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            return AddressIn.model_validate(value)
        except ValueError:
            return None


class Plan(BaseModel):
    """The only thing the planner LLM may return. No amount, no order id of its own: code fills those."""

    model_config = ConfigDict(extra="ignore")
    intent: PlanIntent
    action: PlanAction
    tool: WriteTool | None = None
    args: PlanArgs = Field(default_factory=PlanArgs)
    confidence: float = Field(ge=0, le=1)
    sentiment: float = Field(default=0.0, ge=-1, le=1)
    language: Language = "hinglish"
    summary: Annotated[str, _clip(400)] = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _percent(cls, value: Any) -> Any:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
        return number / 100 if 1 < number <= 100 else number

    @field_validator("sentiment", mode="before")
    @classmethod
    def _clamp_sentiment(cls, value: Any) -> Any:
        try:
            return max(-1.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("language", mode="before")
    @classmethod
    def _lang(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text in ("hi", "en", "hinglish") else "hinglish"

    @model_validator(mode="after")
    def _shape(self) -> "Plan":
        if self.action == "propose":
            if self.tool is None:
                raise ValueError("propose needs a tool")
            if not self.args.order_ref:
                raise ValueError("write tools need order_ref")
            if self.tool == "exchange_request" and not (
                self.args.line_item and self.args.new_variant
            ):
                raise ValueError("exchange needs line_item and new_variant")
            if self.tool == "address_update" and self.args.new_address is None:
                raise ValueError("address_update needs a valid new_address")
        elif self.tool is not None:
            raise ValueError("tool only allowed with action=propose")
        return self


class ReplyDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1, max_length=1500)
    language: Language = "hinglish"
    facts_used: list[str] = Field(default_factory=list, max_length=30)
    promises: list[Promise] = Field(default_factory=list, max_length=5)
    needs_human: bool = False

    @field_validator("text", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("language", mode="before")
    @classmethod
    def _lang(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text in ("hi", "en", "hinglish") else "hinglish"


Awaiting = Literal["confirm", "address", "order_ref", "none"]


class ConversationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending_action_id: str | None = None
    awaiting: Awaiting = "none"
    turn_count: int = Field(default=0, ge=0)
    unclear_count: int = Field(default=0, ge=0)
    write_count: int = Field(default=0, ge=0)


class HandoffSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_masked_phone: str | None = None
    order_ref: str | None = None
    customer_asked: str = Field(max_length=400)
    agent_did: list[str] = Field(default_factory=list, max_length=10)
    reason_code: ReasonCodeV1
    why_stuck: str = Field(max_length=300)
    language: Language = "hinglish"


class V1Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(max_length=60)
    language: Language
    text: str = Field(min_length=1, max_length=1500)
    facts_used: list[str] = Field(default_factory=list)


class WriteIntent(BaseModel):
    """What the planner graph wants done. Amounts and ids come from order facts, never the LLM."""

    model_config = ConfigDict(extra="forbid")
    tool: WriteTool
    order_ref: str = Field(max_length=40)
    amount_paise: int = Field(ge=0)
    route: Literal["auto", "approval"]
    gate_reason: str = Field(max_length=60)
    args: dict[str, Any] = Field(default_factory=dict)


class ConvUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    awaiting: Awaiting = "none"
    unclear_count: int = Field(default=0, ge=0)
    turn_count: int = Field(default=0, ge=0)
    clear_pending: bool = False
    order_ref: str | None = None


class DecisionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reply", "handoff", "skip"]
    reason_code: ReasonCodeV1 | None = None
    priority: Literal["normal", "urgent"] = "normal"
    reply: V1Reply | None = None
    handoff: HandoffSummaryV1 | None = None
    intent: WriteIntent | None = None
    confirm: Literal["yes", "no"] | None = None
    conv: ConvUpdate = Field(default_factory=ConvUpdate)

    @model_validator(mode="after")
    def _shape(self) -> "DecisionV1":
        if self.action == "handoff" and (self.handoff is None or self.reason_code is None):
            raise ValueError("handoff needs summary and reason_code")
        if self.action == "skip" and (self.reply or self.handoff or self.intent or self.confirm):
            raise ValueError("skip carries nothing")
        if self.action == "reply" and self.reply is None and self.confirm is None:
            raise ValueError("reply needs text or a confirm answer")
        if self.intent is not None and self.confirm is not None:
            raise ValueError("propose and confirm are different turns")
        return self
