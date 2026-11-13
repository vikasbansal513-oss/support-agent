import uuid
from collections.abc import Iterable
from datetime import date, time
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.contracts import (
    DEFAULT_DISCLOSURE,
    HHMM_RE,
    V0_TOOLS,
    Disclosure,
    OfficeHours,
    PolicyV1,
)
from app.v1.contracts import WRITE_TOOLS, Level, WriteTool

ToolNameV2 = Literal[
    "order_lookup",
    "tracking_lookup",
    "handoff_to_human",
    "address_update",
    "order_cancel",
    "exchange_request",
    "return_request",
    "refund_create",
]
V2_TOOLS: tuple[str, ...] = (*V0_TOOLS, *WRITE_TOOLS)


class Autonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_status: Literal["auto"] = "auto"
    address_update: Level = "off"
    order_cancel: Level = "off"
    exchange_request: Level = "off"
    return_request: Level = "off"
    refund_create: Level = "off"

    def level(self, tool: str) -> Level:
        if tool not in WRITE_TOOLS:
            return "auto"
        value: Level = getattr(self, tool)
        return value


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancel_auto_limit_paise: int = Field(default=200_000, gt=0)
    exchange_auto_limit_paise: int = Field(default=200_000, gt=0)
    return_auto_limit_paise: int = Field(default=200_000, gt=0)
    refund_auto_limit_paise: int = Field(default=50_000, gt=0)
    refund_customer_daily_paise: int = Field(default=100_000, gt=0)
    brand_daily_auto_value_paise: int = Field(default=2_000_000, gt=0)
    high_value_threshold_paise: int = Field(default=500_000, gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "Limits":
        if self.refund_customer_daily_paise < self.refund_auto_limit_paise:
            raise ValueError("refund_customer_daily_paise cannot be below refund_auto_limit_paise")
        if self.brand_daily_auto_value_paise < max(
            self.cancel_auto_limit_paise,
            self.exchange_auto_limit_paise,
            self.refund_auto_limit_paise,
        ):
            raise ValueError("brand_daily_auto_value_paise cannot be below a single auto limit")
        return self

    def auto_limit(self, tool: str) -> int | None:
        return {
            "order_cancel": self.cancel_auto_limit_paise,
            "exchange_request": self.exchange_auto_limit_paise,
            "refund_create": self.refund_auto_limit_paise,
        }.get(tool)


class Faq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=200)
    answer: str = Field(min_length=3, max_length=400)

    @field_validator("question", "answer")
    @classmethod
    def _strip(cls, v: str) -> str:
        text = " ".join(v.split())
        if len(text) < 3:
            raise ValueError("too short")
        return text


class DayHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not HHMM_RE.match(v):
            raise ValueError("use HH:MM, 24 hour")
        return v

    @model_validator(mode="after")
    def _order(self) -> "DayHours":
        if time.fromisoformat(self.start) >= time.fromisoformat(self.end):
            raise ValueError("start must be before end")
        return self


def _default_tools() -> list[ToolNameV2]:
    return ["handoff_to_human", "order_lookup", "tracking_lookup"]


class PolicyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    office_hours: OfficeHours = Field(default_factory=OfficeHours)
    return_window_days: int = Field(default=7, ge=1, le=60)
    final_sale_tags: list[str] = Field(default_factory=list, max_length=50)
    # Optional shorter/longer return window for orders carrying a given tag. When an order
    # has several matching tags the strictest (smallest) window wins; no match means
    # return_window_days. Empty = old single-window behaviour.
    return_window_by_tag: dict[str, int] = Field(default_factory=dict)
    customer_daily_msg_cap: int = Field(default=30, ge=1, le=500)
    brand_daily_msg_cap: int = Field(default=2000, ge=1, le=100000)
    handoff_sla_minutes: int = Field(default=30, ge=5, le=1440)
    ai_disclosure: Disclosure = DEFAULT_DISCLOSURE
    # Agent_Reply_TopClass_Notes.md Section 3b: every reply used to go out in English
    # regardless of what the customer wrote in, by a hardcoded constant
    # (agent/validate.py::pick_language()). That is a real product risk for Indian
    # D2C brands whose end-customers write in Hindi/Hinglish. This is now the brand's
    # own choice (Dashboard -> Policy page), default "en" so existing behaviour is
    # unchanged until a brand opts in.
    reply_language: Literal["hi", "en", "hinglish"] = "en"
    # Closed dates (YYYY-MM-DD) and per-weekday hours ("0".."6", Mon=0). Both are optional;
    # empty means the old single office_hours rule applies unchanged.
    holidays: list[str] = Field(default_factory=list, max_length=60)
    day_hours: dict[str, DayHours] = Field(default_factory=dict)
    # Brand-written answers to common store questions. The reply writer may only repeat these
    # (they go in as facts), so the agent never invents a shipping time or payment rule.
    faqs: list[Faq] = Field(default_factory=list, max_length=20)
    # Extra line added to the "we are closed" acknowledgement when a customer is handed off
    # outside office hours.
    ooo_message: str = Field(default="", max_length=300)
    # How Cash on Delivery orders are refunded. This is information for the customer reply only;
    # it does not move money and the agent does not detect COD orders.
    cod_refund_method: Literal["", "bank_transfer", "store_credit"] = ""
    cod_refund_note: str = Field(default="", max_length=200)
    # 0 = use the platform default. Otherwise remind by email/WhatsApp every N minutes while a
    # handoff is past its SLA.
    handoff_reminder_minutes: int = Field(default=0, ge=0, le=1440)
    extra_handoff_keywords: list[str] = Field(default_factory=list, max_length=100)
    # Email_Filter_v2.2.md Section 15: per-brand override of the classifier's default
    # action for a given email category (e.g. one brand auto-skips wholesale enquiries,
    # another wants them processed). Checked after the LLM returns its category.
    email_filter_overrides: dict[str, Literal["PROCESS", "SKIP", "MANUAL_REVIEW"]] = Field(
        default_factory=dict
    )
    enabled_tools: list[ToolNameV2] = Field(default_factory=lambda: _default_tools())
    autonomy: Autonomy = Field(default_factory=Autonomy)
    limits: Limits = Field(default_factory=Limits)
    confirm_ttl_minutes: int = Field(default=30, ge=5, le=240)
    max_tool_steps: int = Field(default=4, ge=1, le=4)
    max_write_actions: int = Field(default=3, ge=1, le=3)
    sentiment_min: float = Field(default=-0.4, ge=-1, le=0)
    tone_notes: str = Field(default="", max_length=500)
    # Decision Framework Section 12.4: "several similar claims clustering in a short time
    # window" - individually ordinary, together the signature of organised refund-ring
    # abuse. A customer with this many refund/exchange/return claims inside the window is
    # routed to a human on every one of those tools, regardless of mode or ₹ limit.
    fraud_cluster_window_hours: int = Field(default=24, ge=1, le=168)
    fraud_cluster_claim_limit: int = Field(default=3, ge=2, le=20)

    @property
    def refund_auto_limit_paise(self) -> int | None:
        if self.autonomy.refund_create == "off" or "refund_create" not in self.enabled_tools:
            return None
        return self.limits.refund_auto_limit_paise

    @property
    def refund_customer_daily_limit_paise(self) -> int | None:
        return (
            None
            if self.refund_auto_limit_paise is None
            else self.limits.refund_customer_daily_paise
        )

    @field_validator("final_sale_tags", "extra_handoff_keywords")
    @classmethod
    def _norm_list(cls, value: list[str]) -> list[str]:
        out = []
        for item in value:
            text = " ".join(item.lower().split())
            if not text or len(text) > 60:
                raise ValueError("entries must be 1..60 chars")
            out.append(text)
        return sorted(set(out))

    @field_validator("handoff_reminder_minutes")
    @classmethod
    def _reminder(cls, value: int) -> int:
        if 0 < value < 5:
            raise ValueError("handoff_reminder_minutes must be 0 (platform default) or at least 5")
        return value

    @field_validator("ooo_message", "cod_refund_note")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("return_window_by_tag")
    @classmethod
    def _window_tags(cls, value: dict[str, int]) -> dict[str, int]:
        if len(value) > 50:
            raise ValueError("at most 50 tag windows")
        out: dict[str, int] = {}
        for key, days in value.items():
            tag = " ".join(key.lower().split())
            if not tag or len(tag) > 60:
                raise ValueError("tags must be 1..60 chars")
            if not 1 <= days <= 60:
                raise ValueError("tag return window must be 1..60 days")
            out[tag] = days
        return out

    @field_validator("holidays")
    @classmethod
    def _holidays(cls, value: list[str]) -> list[str]:
        try:
            return sorted({date.fromisoformat(item).isoformat() for item in value})
        except ValueError as exc:
            raise ValueError("holidays must be YYYY-MM-DD dates") from exc

    @field_validator("day_hours")
    @classmethod
    def _day_hours(cls, value: dict[str, DayHours]) -> dict[str, DayHours]:
        if any(key not in {"0", "1", "2", "3", "4", "5", "6"} for key in value):
            raise ValueError("day_hours keys must be 0..6 (Mon=0)")
        return value

    @field_validator("enabled_tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        if "handoff_to_human" not in value:
            raise ValueError("handoff_to_human cannot be disabled")
        return sorted(set(value))

    @model_validator(mode="after")
    def _levels_need_tools(self) -> "PolicyV2":
        for tool in WRITE_TOOLS:
            if self.autonomy.level(tool) != "off" and tool not in self.enabled_tools:
                raise ValueError(f"{tool} is {self.autonomy.level(tool)} but not in enabled_tools")
        return self

    def return_window_for(self, tags: Iterable[str]) -> int:
        normalised = {" ".join(t.lower().split()) for t in tags}
        days = [d for tag, d in self.return_window_by_tag.items() if tag in normalised]
        return min(days) if days else self.return_window_days

    def write_level(self, tool: WriteTool) -> Level:
        return "off" if tool not in self.enabled_tools else self.autonomy.level(tool)


AnyPolicy = PolicyV1 | PolicyV2


def upgrade(old: PolicyV1) -> PolicyV2:
    base: dict[str, Any] = old.model_dump(mode="json")
    refund_auto = base.pop("refund_auto_limit_paise")
    refund_daily = base.pop("refund_customer_daily_limit_paise")
    base.pop("schema_version")
    limits = Limits()
    if refund_auto is not None and refund_daily is not None:
        limits = Limits(
            refund_auto_limit_paise=refund_auto,
            refund_customer_daily_paise=refund_daily,
            brand_daily_auto_value_paise=max(Limits().brand_daily_auto_value_paise, refund_daily),
        )
    return PolicyV2.model_validate({**base, "schema_version": 2, "limits": limits.model_dump()})


def parse_policy(content: dict[str, Any]) -> PolicyV2:
    version = content.get("schema_version", 1)
    if version == 2:
        return PolicyV2.model_validate(content)
    return upgrade(PolicyV1.model_validate(content))


def parse_any(content: dict[str, Any]) -> AnyPolicy:
    return (
        PolicyV2.model_validate(content)
        if content.get("schema_version") == 2
        else PolicyV1.model_validate(content)
    )


class PolicyOutAny(BaseModel):
    id: uuid.UUID
    version: int
    status: Literal["draft", "published", "archived"]
    content: AnyPolicy
    created_at: AwareDatetime
    published_at: AwareDatetime | None
    regression_run_id: uuid.UUID | None


class PolicyDraftAnyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: AnyPolicy
    base_version: int | None = None
    reason: str | None = Field(default=None, max_length=300)


def raised_levels(new: PolicyV2, published: PolicyV2 | None) -> list[str]:
    """Write tools where the owner's draft turns autonomy up to `auto` beyond what the admin enabled."""
    raised: list[str] = []
    for tool in WRITE_TOOLS:
        if new.autonomy.level(tool) == "auto" and (
            published is None or published.autonomy.level(tool) != "auto"
        ):
            raised.append(tool)
    return raised


_LIMIT_TOOL = {
    "cancel_auto_limit_paise": "order_cancel",
    "exchange_auto_limit_paise": "exchange_request",
    "return_auto_limit_paise": "return_request",
    "refund_auto_limit_paise": "refund_create",
    "refund_customer_daily_paise": "refund_create",
}


def limits_raised_under_auto(new: PolicyV2, published: PolicyV2) -> list[str]:
    """Limit fields the owner raised while the tool runs on its own: only an admin may do that."""
    risen: list[str] = []
    for name, tool in _LIMIT_TOOL.items():
        if new.autonomy.level(tool) != "auto":
            continue
        if getattr(new.limits, name) > getattr(published.limits, name):
            risen.append(name)
    if (
        new.limits.brand_daily_auto_value_paise > published.limits.brand_daily_auto_value_paise
        and any(new.autonomy.level(t) == "auto" for t in WRITE_TOOLS)
    ):
        risen.append("brand_daily_auto_value_paise")
    if new.limits.high_value_threshold_paise > published.limits.high_value_threshold_paise and any(
        new.autonomy.level(t) == "auto" for t in WRITE_TOOLS
    ):
        risen.append("high_value_threshold_paise")
    return risen
