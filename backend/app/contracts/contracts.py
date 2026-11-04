"""Support Agent V0: exact data contracts (Pydantic v2). Claude Code copies these into app/ (see TDD Section 5.1).

Rules: money is integer paise; times are timezone-aware UTC datetimes; every LLM-facing model uses extra="ignore"
and clips strings; every API input model uses extra="forbid".
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, time
from typing import Annotated, Any, Literal, TypedDict

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

# ------------------------------------------------------------------ enums
Language = Literal["hi", "en", "hinglish"]
Channel = Literal["whatsapp", "email"]
Mode = Literal["shadow", "draft", "auto"]
TenantStatus = Literal["pending_approval", "active", "paused", "suspended"]
Role = Literal["owner", "agent"]
IntegrationKind = Literal[
    "shopify",
    "gupshup",
    "gmail",
    "shiprocket",
    "delhivery",
    "bluedart",
    "dtdc",
    "ecom_express",
    "xpressbees",
    "shadowfax",
]
Intent = Literal[
    "order_status", "tracking", "other"
]  # V0. V1 adds refund, cancel, exchange, address_change
Sentiment = Literal["normal", "angry"]
ActionStatus = Literal[
    "claimed", "shadow", "draft", "sent", "failed", "needs_review", "rejected", "suppressed"
]
HandoffStatus = Literal["open", "claimed", "resolved"]
ToolName = Literal["order_lookup", "tracking_lookup", "handoff_to_human"]
Decision = Literal["reply", "handoff", "skip"]

REASON_CODES = (
    "AGENT_PAUSED",
    "OPTED_OUT",
    "HUMAN_ACTIVE",
    "MEDIA_RECEIVED",
    "HUMAN_REQUESTED",
    "ANGRY",
    "SAFETY",
    "LEGAL",
    "MEDICAL",
    "FRAUD",
    "PRESS",
    "POLICY_MISSING",
    "LLM_UNAVAILABLE",
    "LLM_AUTH_ERROR",
    "INVALID_LLM_OUTPUT",
    "LOW_CONFIDENCE",
    "MIXED_INTENT",
    "REPEAT_COMPLAINT",
    "UNSUPPORTED_INTENT",
    "IDENTITY_FAILED",
    "TOOL_FAILED",
    "REPLY_VALIDATION_FAILED",
    "WINDOW_CLOSED",
    "CUSTOMER_REPORT",
)
ReasonCode = Literal[
    "AGENT_PAUSED",
    "OPTED_OUT",
    "HUMAN_ACTIVE",
    "MEDIA_RECEIVED",
    "HUMAN_REQUESTED",
    "ANGRY",
    "SAFETY",
    "LEGAL",
    "MEDICAL",
    "FRAUD",
    "PRESS",
    "POLICY_MISSING",
    "LLM_UNAVAILABLE",
    "LLM_AUTH_ERROR",
    "INVALID_LLM_OUTPUT",
    "LOW_CONFIDENCE",
    "MIXED_INTENT",
    "REPEAT_COMPLAINT",
    "UNSUPPORTED_INTENT",
    "IDENTITY_FAILED",
    "TOOL_FAILED",
    "REPLY_VALIDATION_FAILED",
    "WINDOW_CLOSED",
    "CUSTOMER_REPORT",
]
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
# Handoffs from the V0 and V1 graphs land in the same `handoffs` table and are read back
# through the same API (HandoffOut/HandoffSummary below), so those response models must
# accept either graph's reason codes, not just V0's.
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
URGENT_REASONS = frozenset(
    {"ANGRY", "SAFETY", "LEGAL", "MEDICAL", "FRAUD", "PRESS", "REPEAT_COMPLAINT"}
)
TEMPLATE_IDS = (
    "order_status",
    "tracking_status",
    "ask_order_ref",
    "order_not_found_reask",
    "tool_unavailable_ack",
    "handoff_ack",
    "off_hours_ack",
    "media_ack",
    "human_requested_ack",
    "ai_disclosure_line",
    "ai_identity_answer",
    "opt_out_confirm",
)
V0_TOOLS = ("order_lookup", "tracking_lookup", "handoff_to_human")

ORDER_REF_RE = re.compile(r"^#?[A-Za-z0-9][A-Za-z0-9-]{0,19}$")
E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def _clip(limit: int):
    def clip(value: Any) -> Any:
        if value is None:
            return None
        return (" ".join(str(value).split())[:limit]) or None

    return BeforeValidator(clip)


ShortText = Annotated[str | None, _clip(300)]


# ------------------------------------------------------------------ errors (every API error body)
class ErrorBody(BaseModel):
    code: str = Field(pattern=r"^E-[A-Z]+-\d{3}$")
    message: str
    error_id: str = Field(pattern=r"^ERR-[0-9A-F]{12}$")
    severity: Literal["critical", "error", "warning"] = "error"


# ------------------------------------------------------------------ channel layer
class NormalizedInbound(BaseModel):
    """channels/normalize.py output. Same shape for WhatsApp and Gmail."""

    model_config = ConfigDict(extra="forbid")
    channel: Channel
    external_id: str = Field(min_length=1, max_length=200)
    from_phone: str | None = None
    from_email: str | None = None
    text: str = Field(default="", max_length=20000)
    media_type: (
        Literal["image", "audio", "video", "document", "sticker", "location", "other"] | None
    ) = None
    email_headers: dict[str, str] = Field(default_factory=dict)
    email_subject: str | None = None
    email_thread_id: str | None = None
    received_at: AwareDatetime

    @model_validator(mode="after")
    def _one_sender(self) -> "NormalizedInbound":
        if self.channel == "whatsapp":
            if not self.from_phone or not E164_RE.match(self.from_phone):
                raise ValueError("whatsapp inbound needs from_phone in E.164 (+91...)")
        elif not self.from_email:
            raise ValueError("email inbound needs from_email")
        return self


# ------------------------------------------------------------------ LLM output (the only thing the LLM may return)
class ClassifyFlags(BaseModel):
    model_config = ConfigDict(extra="ignore")
    asks_if_human: bool = False
    wants_human: bool = False
    mentions_legal: bool = False
    mentions_medical: bool = False
    mentions_fraud: bool = False
    mentions_press: bool = False
    mentions_safety: bool = False


class Classification(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: Intent
    order_ref: ShortText = None
    confidence: float = Field(ge=0, le=1)
    language: Language = "hinglish"
    sentiment: Sentiment = "normal"
    flags: ClassifyFlags = Field(default_factory=ClassifyFlags)
    mixed_intents: list[Intent] = Field(default_factory=list, max_length=5)
    summary: Annotated[str, _clip(400)] = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _percent(cls, v: Any) -> Any:
        try:
            n = float(v)
        except (TypeError, ValueError):
            return v
        return n / 100 if 1 < n <= 100 else n

    @field_validator("language", mode="before")
    @classmethod
    def _lang(cls, v: Any) -> str:
        t = str(v or "").strip().lower()
        return t if t in ("hi", "en", "hinglish") else "hinglish"

    @field_validator("order_ref", mode="after")
    @classmethod
    def _order_ref(cls, v: str | None) -> str | None:
        """LLM order id must look like an order id; otherwise dropped (INT-03). Code may also extract by regex."""
        if v is None:
            return None
        v = v.strip()
        return v if ORDER_REF_RE.match(v) and any(c.isdigit() for c in v) else None

    @field_validator("mixed_intents", mode="before")
    @classmethod
    def _mixed(cls, v: Any) -> list[str]:
        return (
            [x for x in v if x in ("order_status", "tracking", "other")]
            if isinstance(v, list)
            else []
        )


# ------------------------------------------------------------------ tool facts (what tools return; the ONLY source of reply facts)
class OrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=200)
    quantity: int = Field(ge=1, le=10000)


class OrderFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_name: str = Field(max_length=40)  # "#1001"
    financial_status: str = Field(max_length=40)  # paid | pending | refunded ...
    fulfillment_status: str = Field(max_length=40)  # unfulfilled | fulfilled | partial
    lines: list[OrderLine] = Field(max_length=50)
    total_paise: int = Field(ge=0)
    created_at: AwareDatetime
    tracking_numbers: list[str] = Field(default_factory=list, max_length=10)
    courier: str | None = None


class TrackingFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    awb: str = Field(max_length=60)
    courier: str
    status_text: str = Field(max_length=120)
    last_update_at: AwareDatetime | None = None
    expected_delivery: AwareDatetime | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: ToolName
    ok: bool
    error_code: (
        Literal["not_found", "customer_mismatch", "upstream_error", "not_configured", "auth_error"]
        | None
    ) = None
    order: OrderFacts | None = None
    tracking: TrackingFacts | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "ToolResult":
        if self.ok and self.error_code:
            raise ValueError("ok result cannot have error_code")
        if not self.ok and (self.order or self.tracking):
            raise ValueError("failed result cannot carry facts")
        return self


# ------------------------------------------------------------------ agent state and decision
class HandoffSummary(BaseModel):
    """FR-HO-01. Phone is masked, never raw."""

    model_config = ConfigDict(extra="forbid")
    customer_masked_phone: str | None = None
    order_ref: str | None = None
    customer_asked: str = Field(max_length=400)
    agent_did: list[str] = Field(default_factory=list, max_length=10)
    reason_code: ReasonCodeV1
    why_stuck: str = Field(max_length=300)
    language: Language = "hinglish"


class ReplySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: Literal[TEMPLATE_IDS]  # type: ignore[valid-type]
    language: Language
    text: str = Field(min_length=1, max_length=1500)
    facts_used: list[str] = Field(default_factory=list)


class DecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Decision
    reason_code: ReasonCode | None = None
    priority: Literal["normal", "urgent"] = "normal"
    reply: ReplySpec | None = None
    handoff: HandoffSummary | None = None

    @model_validator(mode="after")
    def _shape(self) -> "DecisionOut":
        if self.action == "handoff" and (self.handoff is None or self.reason_code is None):
            raise ValueError("handoff decision needs handoff summary and reason_code")
        if self.action == "reply" and self.reply is None:
            raise ValueError("reply decision needs reply")
        if self.action == "skip" and (self.reply or self.handoff):
            raise ValueError("skip decision carries nothing")
        return self


class AgentState(TypedDict, total=False):
    tenant_id: str
    run_id: str
    inbound_event_id: str
    conversation_id: str
    customer_id: str
    channel: str
    incoming_text: str
    media_type: str | None
    history: list[dict[str, str]]
    policy: dict[str, Any] | None
    tenant_mode: str
    agent_paused: bool
    human_active: bool
    opted_out: bool
    first_message: bool
    within_office_hours: bool
    language: str
    classification: dict[str, Any] | None
    flags: list[str]
    identity: dict[str, Any]
    tool_results: dict[str, dict[str, Any]]
    tool_errors: list[str]
    reply: dict[str, Any] | None
    decision: dict[str, Any]
    trace: list[dict[str, Any]]


# ------------------------------------------------------------------ policy (versioned JSON; TDD 7.10)
class OfficeHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str = "09:00"
    end: str = "21:00"
    days: list[int] = Field(
        default_factory=lambda: [0, 1, 2, 3, 4, 5, 6], min_length=1, max_length=7
    )  # Mon=0

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not HHMM_RE.match(v):
            raise ValueError("use HH:MM, 24 hour")
        return v

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v) or len(set(v)) != len(v):
            raise ValueError("days must be unique ints 0..6")
        return sorted(v)

    @model_validator(mode="after")
    def _order(self) -> "OfficeHours":
        if time.fromisoformat(self.start) >= time.fromisoformat(self.end):
            raise ValueError("start must be before end (overnight shifts are not supported in V0)")
        return self


class Disclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hi: str = Field(min_length=5, max_length=300)
    en: str = Field(min_length=5, max_length=300)
    hinglish: str = Field(min_length=5, max_length=300)


DEFAULT_DISCLOSURE = Disclosure(
    hi="Main ek AI sahayak hoon. Zarurat padne par hamari team aapse baat karegi.",
    en="I'm an AI assistant. Our team will step in if needed.",
    hinglish="Main ek AI assistant hoon. Zarurat padi to hamari team aapse baat karegi.",
)


class PolicyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    office_hours: OfficeHours = Field(default_factory=OfficeHours)
    return_window_days: int = Field(default=7, ge=1, le=60)
    final_sale_tags: list[str] = Field(default_factory=list, max_length=50)
    refund_auto_limit_paise: int | None = Field(default=None, gt=0)  # None = auto refund OFF
    refund_customer_daily_limit_paise: int | None = Field(
        default=None, gt=0
    )  # None = auto refund OFF
    customer_daily_msg_cap: int = Field(default=30, ge=1, le=500)
    brand_daily_msg_cap: int = Field(default=2000, ge=1, le=100000)
    handoff_sla_minutes: int = Field(default=30, ge=5, le=1440)
    ai_disclosure: Disclosure = DEFAULT_DISCLOSURE
    extra_handoff_keywords: list[str] = Field(default_factory=list, max_length=100)
    enabled_tools: list[ToolName] = Field(default_factory=lambda: sorted(V0_TOOLS))

    @field_validator("final_sale_tags", "extra_handoff_keywords")
    @classmethod
    def _norm_list(cls, v: list[str]) -> list[str]:
        out = []
        for x in v:
            t = " ".join(x.lower().split())
            if not t or len(t) > 60:
                raise ValueError("entries must be 1..60 chars")
            out.append(t)
        return sorted(set(out))

    @field_validator("enabled_tools")
    @classmethod
    def _tools(cls, v: list[str]) -> list[str]:
        if "handoff_to_human" not in v:
            raise ValueError("handoff_to_human cannot be disabled")
        return sorted(set(v))

    @model_validator(mode="after")
    def _refund_pair(self) -> "PolicyV1":
        a, b = self.refund_auto_limit_paise, self.refund_customer_daily_limit_paise
        if (a is None) != (b is None):
            raise ValueError(
                "set both refund limits or neither (auto refund stays OFF if either is missing)"
            )
        if a is not None and b is not None and b < a:
            raise ValueError("customer daily limit cannot be below the per-refund limit")
        return self


# ------------------------------------------------------------------ API: shared
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None


# ------------------------------------------------------------------ API: auth (shapes follow GST api/auth.py)
class SignupIn(Strict):
    brand_name: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class LoginIn(Strict):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class EmailIn(Strict):
    email: str = Field(max_length=254)


class TokenIn(Strict):
    token: str = Field(min_length=10, max_length=200)


class ResetIn(Strict):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=10, max_length=128)


class ChangePasswordIn(Strict):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=10, max_length=128)


class MfaVerifyIn(Strict):
    mfa_token: str = Field(min_length=20, max_length=2000)
    code: str = Field(min_length=6, max_length=20)


class CodeIn(Strict):
    code: str = Field(min_length=6, max_length=20)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: Role | Literal["super_admin"]
    tenant_id: uuid.UUID | None
    tenant_name: str | None = None
    mfa_ok: bool


class LoginOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    csrf_token: str | None
    user: UserOut


class MfaChallengeOut(BaseModel):
    mfa_required: Literal[True] = True
    mfa_token: str
    expires_in: int


class StatusOut(BaseModel):
    status: str


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodesOut(BaseModel):
    enabled: bool = True
    recovery_codes: list[str]


class MfaStatusOut(BaseModel):
    enabled: bool
    required: bool
    recovery_remaining: int


# ------------------------------------------------------------------ API: admin
class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: TenantStatus
    agent_paused: bool
    mode: Mode
    plan: str
    included_conversations: int
    usage_override_limit: int | None
    created_at: AwareDatetime
    approved_at: AwareDatetime | None


class TenantPatchIn(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class ModeIn(Strict):
    mode: Mode
    reason: str = Field(min_length=5, max_length=300)


class UsageLimitIn(Strict):
    usage_override_limit: int | None = Field(ge=0, le=1_000_000)
    reason: str = Field(min_length=5, max_length=300)


class ReasonIn(Strict):
    reason: str = Field(min_length=5, max_length=300)


class JobOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: str
    status: Literal["queued", "deferred", "processing", "done", "failed", "needs_review"]
    attempts: int
    error_code: str | None
    error_id: str | None
    created_at: AwareDatetime


class LlmUsageRowOut(BaseModel):
    tenant_id: uuid.UUID
    day: str
    calls: int
    input_tokens: int
    output_tokens: int
    est_cost_micro: int | None
    p95_latency_ms: int | None


class CallsByDayOut(BaseModel):
    day: str
    calls: int


class OverviewOut(BaseModel):
    tenants_total: int
    tenants_active: int
    tenants_pending: int
    tenants_paused: int
    handoffs_open: int
    # jobs.status = 'failed'. jobs_needs_review is the separate "needs a human look" bucket
    # (jobs_failed counted status='needs_review' before 2026-11-02, see DECISIONS.md).
    jobs_failed: int
    jobs_needs_review: int
    today_calls: int
    today_cost_micro: int
    calls_by_day: list[CallsByDayOut]


class AdminHealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "down"]
    worker_last_seen_at: AwareDatetime | None
    worker_seconds_since: int | None
    queue_depth: int
    # jobs.status = 'failed' (truly failed). jobs_needs_review_today was folded into this
    # field before 2026-11-02 (it actually counted status='needs_review') — see DECISIONS.md.
    jobs_failed_today: int
    jobs_needs_review_today: int


class TenantStatsOut(BaseModel):
    tenant_id: uuid.UUID
    messages_today: int
    monthly_cost_micro: int
    last_activity_at: AwareDatetime | None


class QuotaOut(BaseModel):
    tenant_id: uuid.UUID
    monthly_call_limit: int | None
    warn_percent: int
    calls_this_month: int
    usage_percent: float | None
    updated_at: AwareDatetime | None


class QuotaIn(Strict):
    monthly_call_limit: int | None = Field(default=None, ge=0, le=10_000_000)
    warn_percent: int = Field(default=80, ge=1, le=100)
    reason: str = Field(min_length=5, max_length=300)


class AlertOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    tenant_name: str | None = None
    kind: str
    severity: Literal["info", "warning", "high", "critical"]
    message: str
    status: Literal["open", "acknowledged", "resolved"]
    service: str = ""
    affected_agent: str | None = None
    error_id: str | None = None
    root_cause: str | None = None
    created_at: AwareDatetime
    acknowledged_at: AwareDatetime | None = None
    resolution_notes: str | None = None
    resolved_at: AwareDatetime | None
    resolution_minutes: float | None = None


class AuditLogRowOut(BaseModel):
    id: uuid.UUID
    occurred_at: AwareDatetime
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_role: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    reason: str | None


class TenantRankOut(BaseModel):
    tenant_id: uuid.UUID
    name: str
    handled: int
    handed_off: int
    handoff_rate_percent: float


class ControlTowerOut(BaseModel):
    health: AdminHealthOut
    error_rate_percent: float
    skipped_count: int
    p95_latency_ms: int | None
    # "failed" here means jobs.status = 'failed' (truly failed, retries exhausted).
    # needs_review_* below is the separate "needs a human look" bucket (was mislabeled
    # as "failed" before 2026-11-02, see DECISIONS.md).
    failed_jobs_today: int
    failed_jobs_avg_7d: float
    needs_review_jobs_today: int
    needs_review_jobs_avg_7d: float
    top_tenants: list[TenantRankOut]


# ------------------------------------------------------------------ API: Control Tower monitoring
AgentTaskStatus = Literal["running", "done", "failed", "skipped"]
Correctness = Literal["correct", "wrong", "partial", "unverified"]


class AgentTaskRowOut(BaseModel):
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    duration_ms: int | None
    task_category: str
    status: AgentTaskStatus
    correctness: Correctness
    handed_off: bool
    llm_cost_micro: int | None
    llm_model: str | None


class AgentTaskListOut(BaseModel):
    items: list[AgentTaskRowOut]
    total: int


class AgentTaskLlmCallOut(BaseModel):
    purpose: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    outcome: str
    created_at: AwareDatetime


class AgentTaskActionOut(BaseModel):
    action_type: str
    status: str
    tool_name: str | None
    body_text: str | None
    error: str | None
    created_at: AwareDatetime


class AgentTaskDetailOut(BaseModel):
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    status: AgentTaskStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    error_id: str | None
    trace: list[dict[str, Any]]
    decision: dict[str, Any] | None
    actions: list[AgentTaskActionOut]
    llm_calls: list[AgentTaskLlmCallOut]
    verdict: str | None
    report_reason: str | None


class AccuracyBucketOut(BaseModel):
    total_evaluated: int
    correct: int
    incorrect: int
    partially_correct: int
    unverified: int
    accuracy_percent: float
    task_success_percent: float
    human_correction_percent: float
    handoff_percent: float


class AccuracyTenantOut(AccuracyBucketOut):
    tenant_id: uuid.UUID
    tenant_name: str


class AccuracyOut(BaseModel):
    overall: AccuracyBucketOut
    by_tenant: list[AccuracyTenantOut]


class PagePerfRowOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    route: str
    samples: int
    avg_load_ms: float | None
    p50_load_ms: int | None
    p95_load_ms: int | None
    p99_load_ms: int | None
    avg_lcp_ms: float | None
    avg_ttfb_ms: float | None
    failed_api_count: int
    status: Literal["good", "slow"]


class PagePerfOut(BaseModel):
    rows: list[PagePerfRowOut]
    slowest_client: str | None
    slowest_page: str | None
    slow_threshold_ms: int


class PagePerfBeaconIn(Strict):
    route: str = Field(min_length=1, max_length=120)
    ttfb_ms: int | None = Field(default=None, ge=0, le=120_000)
    dom_content_loaded_ms: int | None = Field(default=None, ge=0, le=120_000)
    load_ms: int | None = Field(default=None, ge=0, le=120_000)
    lcp_ms: int | None = Field(default=None, ge=0, le=120_000)
    inp_ms: int | None = Field(default=None, ge=0, le=60_000)
    cls: float | None = Field(default=None, ge=0, le=100)
    api_duration_ms: int | None = Field(default=None, ge=0, le=120_000)
    failed_api: bool = False


class ClientTrafficRowOut(BaseModel):
    tenant_id: uuid.UUID
    name: str
    agent_runs_24h: int
    integration_calls_1h: int
    avg_integration_latency_ms: float | None
    failed_integration_calls_1h: int


class TrafficOut(BaseModel):
    total_clients: int
    active_clients: int
    active_users_5m: int
    running_jobs: int
    queue_depth: int
    needs_review_jobs_recent: int
    failed_jobs_recent: int
    db_connections: int
    integration_calls_per_minute: float
    avg_integration_latency_ms: float | None
    p95_integration_latency_ms: int | None
    integration_error_rate_percent: float
    process_rss_mb: float
    by_client: list[ClientTrafficRowOut]
    note: str


class AlertOutExt(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    kind: str
    severity: Literal["info", "warning", "critical"]
    message: str
    status: Literal["open", "resolved"]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class HistoricalPointOut(BaseModel):
    bucket: str
    value: float


class HistoricalSeriesOut(BaseModel):
    name: str
    points: list[HistoricalPointOut]


class HistoricalOut(BaseModel):
    range: Literal["24h", "7d", "30d", "90d"]
    series: list[HistoricalSeriesOut]


class AgentPerformanceRowOut(BaseModel):
    """One (graph_version, task_category) bucket -- the closest thing this single-graph,
    multi-category agent has to a named "agent": graph_version is the agent generation
    (v0.1 info-only, v1.0 write-capable) and task_category is the capability it executed
    (order_status, cancel, refund, handoff:angry, ...). See DECISIONS.md 2026-11-02 "Control
    Tower: agent-wise performance" for why there is no separate agent-identity table."""

    agent_key: str
    graph_version: str
    task_category: str
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    skipped_tasks: int
    sent_replies: int
    correct: int
    incorrect: int
    partially_correct: int
    unverified: int
    total_evaluated: int
    accuracy_percent: float | None
    task_success_percent: float
    human_corrections: int
    human_correction_percent: float
    handoff_count: int
    handoff_percent: float
    avg_execution_ms: float | None
    top_model: str | None
    insufficient_data: bool
    rank: int


class AgentPerformanceOut(BaseModel):
    rows: list[AgentPerformanceRowOut]
    min_evaluated_for_ranking: int


class ProviderErrorCategoryOut(BaseModel):
    error_class: str
    count: int


class ClientProviderRowOut(BaseModel):
    provider: str
    total_calls: int
    successful_calls: int
    failed_calls: int
    success_percent: float
    avg_latency_ms: float | None
    p95_latency_ms: int | None
    health: Literal["healthy", "degraded", "failed", "unknown"]
    last_success_at: AwareDatetime | None
    last_failure_at: AwareDatetime | None
    top_error_categories: list[ProviderErrorCategoryOut] = []


class ClientMonitoringOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_status: str
    window: Literal["24h", "7d", "30d", "90d"]
    runs_total: int
    runs_done: int
    runs_failed: int
    runs_skipped: int
    accuracy: AccuracyBucketOut
    llm_calls_total: int
    llm_calls_failed: int
    llm_avg_latency_ms: float | None
    llm_p95_latency_ms: int | None
    llm_cost_micro: int
    integration_calls_total: int
    integration_calls_failed: int
    integration_avg_latency_ms: float | None
    integration_p95_latency_ms: int | None
    by_provider: list[ClientProviderRowOut]
    worst_agent_key: str | None
    worst_agent_accuracy_percent: float | None
    worst_provider: str | None


class ThresholdOut(BaseModel):
    key: str
    value: float


class ThresholdsIn(Strict):
    values: dict[str, float]


# ------------------------------------------------------------------ API: integrations / settings
class ShopifyIn(Strict):
    shop_domain: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$", max_length=100)
    access_token: str = Field(min_length=10, max_length=300)


class GupshupIn(Strict):
    userid: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=3, max_length=200)
    source_number: str = Field(pattern=r"^[1-9][0-9]{9,14}$")
    app_name: str = Field(default="", max_length=100)


class CourierIn(Strict):
    # Delhivery/Blue Dart/DTDC/Ecom Express/XpressBees/Shadowfax: API token/key.
    # Shiprocket: "email:password".
    credential: str = Field(min_length=6, max_length=400)


IntegrationIn = ShopifyIn | GupshupIn | CourierIn


class IntegrationOut(BaseModel):
    kind: IntegrationKind
    configured: bool
    status: Literal["ok", "auth_error", "disabled", "not_configured"]
    config: dict[str, str]  # non-secret fields only
    secret_hint: str | None  # "Set hai (...abcd)" never the secret
    last_error_code: str | None
    last_checked_at: AwareDatetime | None
    webhook_url: str | None = (
        None  # gupshup only; shown once at creation / rotation (token is hashed in DB)
    )


class ProbeResultOut(BaseModel):
    ok: bool
    detail: str | None = None
    error_code: str | None = None


class GmailCheckNowOut(BaseModel):
    queued: bool
    job_id: uuid.UUID | None = None
    already_running: bool = False


class NotificationSettings(Strict):
    notify_email_enabled: bool
    notify_email: EmailStr | None = None
    notify_whatsapp_enabled: bool
    alert_phone: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    grievance_email: EmailStr | None = None


class AgentStatusOut(BaseModel):
    mode: Mode
    agent_paused: bool
    tenant_status: TenantStatus
    published_policy_version: int | None
    integration_errors: list[IntegrationKind]


# ------------------------------------------------------------------ API: policies
class PolicyOut(BaseModel):
    id: uuid.UUID
    version: int
    status: Literal["draft", "published", "archived"]
    content: PolicyV1
    created_at: AwareDatetime
    published_at: AwareDatetime | None
    regression_run_id: uuid.UUID | None


class PolicyDraftIn(Strict):
    content: PolicyV1
    base_version: int | None = None


class RegressionOut(BaseModel):
    run_id: uuid.UUID
    passed: bool
    scenarios_total: int
    scenarios_failed: list[str]
    eval_subset_passed: bool


# ------------------------------------------------------------------ API: conversations / handoffs / actions
class CustomerOut(BaseModel):
    id: uuid.UUID
    masked_phone: str | None
    masked_email: str | None


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: Literal["in", "out"]
    sender: Literal["customer", "agent", "human", "system"]
    content: str
    created_at: AwareDatetime
    action_status: ActionStatus | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: Channel
    status: Literal["open", "handoff", "resolved", "closed"]
    customer: CustomerOut
    active_order_ref: str | None
    language: Language | None
    human_active: bool
    last_message_at: AwareDatetime | None


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class HumanReplyIn(Strict):
    content: str = Field(min_length=1, max_length=1500)


class HandoffOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    reason_code: ReasonCodeV1
    priority: Literal["normal", "urgent"]
    status: HandoffStatus
    would_handoff: bool
    summary: HandoffSummary
    sla_due_at: AwareDatetime | None
    sla_breached: bool
    created_at: AwareDatetime


class ActionOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    action_type: Literal["reply_customer", "handoff_notify", "tool_call"]
    status: ActionStatus
    body_text: str | None
    customer_message: str | None
    verdict: Literal["correct", "wrong"] | None
    created_at: AwareDatetime


class VerdictIn(Strict):
    verdict: Literal["correct", "wrong"]
    note: str | None = Field(default=None, max_length=500)


class ReportIn(Strict):
    reason: str = Field(min_length=3, max_length=500)


# ------------------------------------------------------------------ API: dashboard / usage / privacy / public
class DashboardStatsOut(BaseModel):
    day_ist: str
    messages_today: int
    handled_by_agent_today: int
    handed_off_today: int
    open_handoffs: int
    shadow_correct: int
    shadow_wrong: int
    shadow_unreviewed: int
    mode: Mode
    agent_paused: bool


class UsageOut(BaseModel):
    period_start: str
    conversation_count: int
    limit: int
    limit_reached: bool


class PrivacyRequestIn(Strict):
    subject_ref: str = Field(
        min_length=3,
        max_length=200,
        description="customer phone/email to export, delete or correct",
    )
    kind: Literal["export", "delete", "correct"]
    correction_field: Literal["phone", "email"] | None = Field(
        default=None, description="only for kind=correct: which field the customer wants fixed"
    )
    correction_value: str | None = Field(
        default=None, min_length=1, max_length=200, description="only for kind=correct: the corrected value"
    )

    @model_validator(mode="after")
    def _correction_shape(self) -> "PrivacyRequestIn":
        has_correction = self.correction_field is not None or self.correction_value is not None
        if self.kind == "correct":
            if self.correction_field is None or self.correction_value is None:
                raise ValueError("correct requests need correction_field and correction_value")
        elif has_correction:
            raise ValueError("correction_field/correction_value only apply to kind=correct")
        return self


class PrivacyRequestOut(BaseModel):
    id: uuid.UUID
    kind: Literal["export", "delete", "correct"]
    status: Literal["open", "done", "rejected"]
    due_at: AwareDatetime
    created_at: AwareDatetime
    correction_field: Literal["phone", "email"] | None = None
    correction_value: str | None = None


class PublicNoticeOut(BaseModel):
    version: str
    draft: bool
    grievance_email: str | None
    html: str


class HealthOut(BaseModel):
    status: Literal["ok"]


class ReadyOut(BaseModel):
    status: Literal["ready", "not_ready"]
    problems: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ webhook payloads (Gupshup inbound; fields we read)
class GupshupInbound(BaseModel):
    """Gupshup v2 envelope (docs.gupshup.io/docs/inbound-messages). extra=ignore. Inner text/media shape: VERIFY in Stage S4."""

    model_config = ConfigDict(extra="ignore")
    app: str | None = None
    timestamp: int | None = None
    version: int | None = None
    type: str
    payload: dict[str, Any] | None = None


# ------------------------------------------------------------------ API endpoint table (single source for TDD Section 12)
class Endpoint(TypedDict):
    method: str
    path: str
    auth: Literal["public", "secret", "user", "owner", "admin"]
    body: str | None
    returns: str


def E(method: str, path: str, auth: str, body: str | None, returns: str) -> Endpoint:
    return {"method": method, "path": path, "auth": auth, "body": body, "returns": returns}  # type: ignore[typeddict-item]


ENDPOINTS: list[Endpoint] = [
    E("POST", "/auth/signup", "public", "SignupIn", "StatusOut (202)"),
    E("POST", "/auth/verify-email", "public", "TokenIn", "StatusOut"),
    E("POST", "/auth/resend-verification", "public", "EmailIn", "StatusOut (202)"),
    E("POST", "/auth/login", "public", "LoginIn", "LoginOut | MfaChallengeOut"),
    E("POST", "/auth/mfa/verify", "public", "MfaVerifyIn", "LoginOut"),
    E("POST", "/auth/refresh", "public", None, "LoginOut (cookie + X-CSRF-Token)"),
    E("POST", "/auth/logout", "public", None, "204 (cookie + X-CSRF-Token)"),
    E("POST", "/auth/forgot-password", "public", "EmailIn", "StatusOut (202)"),
    E("POST", "/auth/reset-password", "public", "ResetIn", "StatusOut"),
    E("POST", "/auth/change-password", "user", "ChangePasswordIn", "StatusOut"),
    E("GET", "/auth/me", "user", None, "UserOut"),
    E("GET", "/auth/mfa", "user", None, "MfaStatusOut"),
    E("POST", "/auth/mfa/setup", "user", None, "MfaSetupOut"),
    E("POST", "/auth/mfa/confirm", "user", "CodeIn", "RecoveryCodesOut"),
    E("POST", "/auth/mfa/recovery-codes", "user", "CodeIn", "RecoveryCodesOut"),
    E(
        "POST",
        "/admin/auth/login",
        "public",
        "LoginIn",
        "LoginOut | MfaChallengeOut (ADMIN_JWT_SECRET)",
    ),
    E("POST", "/admin/auth/mfa/verify", "public", "MfaVerifyIn", "LoginOut"),
    E("POST", "/admin/auth/refresh", "public", None, "LoginOut"),
    E("POST", "/admin/auth/logout", "public", None, "204"),
    E("GET", "/admin/tenants", "admin", None, "Page[TenantOut]"),
    E("GET", "/admin/tenants/{id}", "admin", None, "TenantOut"),
    E("PATCH", "/admin/tenants/{id}", "admin", "TenantPatchIn", "TenantOut"),
    E("POST", "/admin/tenants/{id}/approve", "admin", "ReasonIn", "TenantOut"),
    E("POST", "/admin/tenants/{id}/pause", "admin", "ReasonIn", "TenantOut"),
    E("POST", "/admin/tenants/{id}/resume", "admin", "ReasonIn", "TenantOut"),
    E("PUT", "/admin/tenants/{id}/mode", "admin", "ModeIn", "TenantOut"),
    E("PUT", "/admin/tenants/{id}/usage-limit", "admin", "UsageLimitIn", "TenantOut"),
    E("POST", "/admin/global-pause", "admin", "ReasonIn", "StatusOut"),
    E("POST", "/admin/global-resume", "admin", "ReasonIn", "StatusOut"),
    E("GET", "/admin/jobs", "admin", None, "Page[JobOut]"),
    E("GET", "/admin/llm-usage", "admin", None, "list[LlmUsageRowOut]"),
    E("GET", "/integrations", "owner", None, "list[IntegrationOut]"),
    E("PUT", "/integrations/shopify", "owner", "ShopifyIn", "IntegrationOut"),
    E("PUT", "/integrations/gupshup", "owner", "GupshupIn", "IntegrationOut"),
    E("PUT", "/integrations/{shiprocket|delhivery}", "owner", "CourierIn", "IntegrationOut"),
    E("POST", "/integrations/{kind}/test", "owner", None, "ProbeResultOut"),
    E("GET", "/settings/notifications", "user", None, "NotificationSettings"),
    E("PUT", "/settings/notifications", "owner", "NotificationSettings", "NotificationSettings"),
    E("GET", "/agent/status", "user", None, "AgentStatusOut"),
    E("POST", "/agent/pause", "owner", "ReasonIn", "AgentStatusOut"),
    E("POST", "/agent/resume", "owner", "ReasonIn", "AgentStatusOut"),
    E("GET", "/policies", "user", None, "list[PolicyOut]"),
    E("POST", "/policies", "owner", "PolicyDraftIn", "PolicyOut (new draft)"),
    E("PUT", "/policies/{id}", "owner", "PolicyDraftIn", "PolicyOut (draft only)"),
    E("POST", "/policies/{id}/publish", "owner", None, "PolicyOut (needs regression pass)"),
    E("GET", "/policies/{id}/regression", "owner", None, "RegressionOut"),
    E("GET", "/conversations", "user", None, "Page[ConversationOut]"),
    E("GET", "/conversations/{id}", "user", None, "ConversationDetailOut"),
    E("POST", "/conversations/{id}/reply", "user", "HumanReplyIn", "MessageOut"),
    E("POST", "/conversations/{id}/resolve", "user", None, "ConversationOut"),
    E("GET", "/handoffs", "user", None, "Page[HandoffOut]"),
    E("POST", "/handoffs/{id}/claim", "user", None, "HandoffOut"),
    E("POST", "/handoffs/{id}/resolve", "user", None, "HandoffOut"),
    E("GET", "/actions", "user", None, "Page[ActionOut] (?status=shadow|draft)"),
    E("POST", "/actions/{id}/approve", "user", None, "ActionOut"),
    E("POST", "/actions/{id}/reject", "user", None, "ActionOut"),
    E("POST", "/actions/{id}/verdict", "user", "VerdictIn", "ActionOut"),
    E("POST", "/messages/{id}/report", "user", "ReportIn", "HandoffOut"),
    E("GET", "/dashboard/stats", "user", None, "DashboardStatsOut"),
    E("GET", "/usage", "user", None, "UsageOut"),
    E("GET", "/privacy/requests", "owner", None, "list[PrivacyRequestOut]"),
    E("POST", "/privacy/requests", "owner", "PrivacyRequestIn", "PrivacyRequestOut"),
    E("GET", "/privacy/export", "owner", None, "file (JSON)"),
    E("GET", "/public/privacy-notice", "public", None, "PublicNoticeOut"),
    E("GET", "/public/terms", "public", None, "PublicNoticeOut"),
    E(
        "POST",
        "/webhooks/whatsapp/{token}",
        "secret",
        "GupshupInbound",
        "StatusOut (always 200 within 1s; token = tenant locator + credential)",
    ),
    E("GET", "/health", "public", None, "HealthOut"),
    E("GET", "/ready", "public", None, "ReadyOut (503 if RLS role unsafe)"),
]
