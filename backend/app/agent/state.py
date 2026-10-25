from typing import Any

from app.contracts.contracts import AgentState


class State(AgentState, total=False):
    customer_masked: str | None
    customer_phone: str | None
    customer_email: str | None
    active_order_ref: str | None
    opens_label: str | None
    attempt: int
    max_attempts: int
    tool_log: list[dict[str, Any]]
    prior_customer_refs: list[str]
    last_language: str | None
    conv: dict[str, Any]
    gate_ctx: dict[str, Any]
    prior_writes: list[dict[str, Any]]
    plan: dict[str, Any] | None
    gate_order: dict[str, Any] | None
    tool_steps: int
    v1_reply: dict[str, Any] | None
    v1_intent: dict[str, Any] | None
    v1_confirm: str | None
    decision_v1: dict[str, Any]
    reply_attempts: int
    reply_source: str
