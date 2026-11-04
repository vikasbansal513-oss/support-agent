import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.contracts import (
    DEFAULT_DISCLOSURE,
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


def _default_tools() -> list[ToolNameV2]:
    return ["handoff_to_human", "order_lookup", "tracking_lookup"]


class PolicyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2] = 2
    office_hours: OfficeHours = Field(default_factory=OfficeHours)
    return_window_days: int = Field(default=7, ge=1, le=60)
    final_sale_tags: list[str] = Field(default_factory=list, max_length=50)
    customer_daily_msg_cap: int = Field(default=30, ge=1, le=500)
    brand_daily_msg_cap: int = Field(default=2000, ge=1, le=100000)
    handoff_sla_minutes: int = Field(default=30, ge=5, le=1440)
    ai_disclosure: Disclosure = DEFAULT_DISCLOSURE
    extra_handoff_keywords: list[str] = Field(default_factory=list, max_length=100)
    enabled_tools: list[ToolNameV2] = Field(default_factory=lambda: _default_tools())
    autonomy: Autonomy = Field(default_factory=Autonomy)
    limits: Limits = Field(default_factory=Limits)
    confirm_ttl_minutes: int = Field(default=30, ge=5, le=240)
    max_tool_steps: int = Field(default=4, ge=1, le=4)
    max_write_actions: int = Field(default=3, ge=1, le=3)
    sentiment_min: float = Field(default=-0.4, ge=-1, le=0)
    tone_notes: str = Field(default="", max_length=500)

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
