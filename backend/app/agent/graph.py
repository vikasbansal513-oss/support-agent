import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.agent import keywords, templates
from app.agent.state import State
from app.agent.tools import ToolBox
from app.agent.validate import disclosure_variants, pick_language, validate_reply
from app.contracts.contracts import (
    DEFAULT_DISCLOSURE,
    URGENT_REASONS,
    Classification,
    DecisionOut,
    HandoffSummary,
    OrderFacts,
    ReplySpec,
    ToolResult,
    TrackingFacts,
)
from app.core.masking import redact_for_llm
from app.llm.client import LlmClient, LlmInvalid, LlmUnavailable, load_prompt, split_prompt
from app.llm.guard import wrap_untrusted
from app.v1.policy import AnyPolicy, parse_any

GRAPH_VERSION = "v0.1"
CLASSIFY_PROMPT = "classify.v1.md"
CLASSIFY_VERSION = "classify.v1"
MAX_IDENTITY_ATTEMPTS = 3
SILENT_REASONS = frozenset({"AGENT_PAUSED"})
SKIP_REASONS = frozenset({"OPTED_OUT", "HUMAN_ACTIVE"})
WHY_STUCK = {
    "AGENT_PAUSED": "Agent paused hai, customer ko jawab nahi gaya.",
    "MEDIA_RECEIVED": "Customer ne photo, voice ya file bheji.",
    "HUMAN_REQUESTED": "Customer ne insaan se baat maangi.",
    "ANGRY": "Customer naraaz hai.",
    "SAFETY": "Safety ka mamla.",
    "LEGAL": "Customer ne legal baat ki.",
    "MEDICAL": "Customer ne health ya injury ki baat ki.",
    "FRAUD": "Customer ne fraud ya galat charge ki baat ki.",
    "PRESS": "Customer ne media ya social media ki baat ki.",
    "POLICY_MISSING": "Is brand ki policy publish nahi hui.",
    "LLM_UNAVAILABLE": "AI service jawab nahi de payi.",
    "INVALID_LLM_OUTPUT": "AI ka jawab samajh nahi aaya.",
    "LOW_CONFIDENCE": "Message ka matlab clear nahi tha.",
    "MIXED_INTENT": "Ek se zyada cheezein poochhi gayi.",
    "REPEAT_COMPLAINT": "Customer wahi baat dobara pooch raha hai.",
    "UNSUPPORTED_INTENT": "Ye request abhi insaan hi sambhalta hai.",
    "IDENTITY_FAILED": "Order number customer se match nahi hua.",
    "TOOL_FAILED": "Order ya tracking system se jawab nahi mila.",
    "REPLY_VALIDATION_FAILED": "Taiyaar jawab safety check me fail hua.",
    "WINDOW_CLOSED": "24 ghante ki messaging window band hai.",
    "CUSTOMER_REPORT": "Brand ne jawab ko galat bataya.",
}
ACK_BY_REASON = {
    "MEDIA_RECEIVED": "media_ack",
    "HUMAN_REQUESTED": "human_requested_ack",
    "TOOL_FAILED": "tool_unavailable_ack",
    "LLM_UNAVAILABLE": "tool_unavailable_ack",
}


@dataclass
class GraphDeps:
    llm: LlmClient
    tools: ToolBox
    classify_min_confidence: float = 0.7


def _flag(state: State, code: str) -> dict[str, Any]:
    return {"flags": [*state.get("flags", []), code]}


def _trace(state: State, node: str, started: float, note: str = "") -> list[dict[str, Any]]:
    entry = {"node": node, "ms": int((time.monotonic() - started) * 1000), "note": note}
    return [*state.get("trace", []), entry]


def _lang(state: State) -> Literal["hi", "en", "hinglish"]:
    classification = state.get("classification")
    if classification and classification.get("language"):
        return pick_language(classification["language"])
    if state.get("last_language"):
        return pick_language(state["last_language"])
    return pick_language(keywords.detect_language(state.get("incoming_text", "")))


def _policy(state: State) -> AnyPolicy | None:
    raw = state.get("policy")
    return parse_any(raw) if raw else None


def _disclosure_text(state: State, lang: str) -> str:
    policy = _policy(state)
    disclosure = policy.ai_disclosure if policy else DEFAULT_DISCLOSURE
    return str(getattr(disclosure, lang))


def _spec(state: State, template_id: str, **facts: object) -> ReplySpec:
    lang = _lang(state)
    text = templates.render(template_id, lang, **facts)
    used = sorted(facts)
    if state.get("first_message") and template_id != "ai_identity_answer":
        text = f"{_disclosure_text(state, lang)}\n\n{text}"
        used = [*used, "ai_disclosure"]
    return ReplySpec(template_id=template_id, language=lang, text=text, facts_used=used)


def _order(state: State) -> OrderFacts | None:
    raw = state.get("tool_results", {}).get("order")
    return OrderFacts.model_validate(raw) if raw else None


def _tracking(state: State) -> TrackingFacts | None:
    raw = state.get("tool_results", {}).get("tracking")
    return TrackingFacts.model_validate(raw) if raw else None


def build_reply(state: State) -> ReplySpec:
    pending = state.get("reply")
    if pending is None:
        raise ValueError("no reply pending")
    lang = _lang(state)
    order, tracking = _order(state), _tracking(state)
    template_id = pending["template_id"]
    if template_id == "order_status" and order:
        items = ", ".join(f"{line.title} x{line.quantity}" for line in order.lines)[:200]
        return _spec(
            state,
            "order_status",
            order_name=order.order_name,
            items=items or "-",
            fulfillment=templates.status_word(templates.FULFILL, order.fulfillment_status, lang),
            payment=templates.status_word(templates.PAY, order.financial_status, lang),
            total=templates.paise_to_rs(order.total_paise),
        )
    if template_id == "tracking_status" and order and tracking:
        return _spec(
            state,
            "tracking_status",
            order_name=order.order_name,
            courier=tracking.courier,
            awb=tracking.awb,
            status=tracking.status_text,
        )
    if template_id == "ai_identity_answer":
        return _spec(state, "ai_identity_answer", brand=pending.get("brand", "our store"))
    return _spec(state, template_id)


def _uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _handoff(state: State, reason: str) -> DecisionOut:
    classification = state.get("classification")
    asked = (classification or {}).get("summary") or redact_for_llm(
        state.get("incoming_text", ""), 400
    )
    did = [f"{c['tool']}: {'ok' if c['ok'] else c['error']}" for c in state.get("tool_log", [])]
    summary = HandoffSummary(
        customer_masked_phone=state.get("customer_masked"),
        order_ref=(state.get("identity") or {}).get("order_ref"),
        customer_asked=str(asked)[:400] or "-",
        agent_did=did[:10],
        reason_code=reason,
        why_stuck=WHY_STUCK.get(reason, "Insaan ki madad chahiye."),
        language=_lang(state),
    )
    ack: ReplySpec | None = None
    if reason not in SILENT_REASONS:
        template_id = ACK_BY_REASON.get(reason, "handoff_ack")
        policy = _policy(state)
        if template_id == "handoff_ack" and not state.get("within_office_hours", True):
            ack = _spec(state, "off_hours_ack", opens=state.get("opens_label") or "-")
        elif template_id == "handoff_ack":
            ack = _spec(state, "handoff_ack", sla=str(policy.handoff_sla_minutes if policy else 30))
        else:
            ack = _spec(state, template_id)
    return DecisionOut(
        action="handoff",
        reason_code=reason,
        priority="urgent" if reason in URGENT_REASONS else "normal",
        reply=ack,
        handoff=summary,
    )


async def _track(tools: ToolBox, order: OrderFacts) -> ToolResult:
    return await tools.tracking_lookup(order.tracking_numbers[0], order.courier)


def _timed(name: str, fn: Callable[[State], Awaitable[dict[str, Any]]]) -> Any:
    async def wrapper(state: State) -> dict[str, Any]:
        started = time.monotonic()
        update = await fn(state)
        update["trace"] = _trace(state, name, started, update.pop("_note", ""))
        return update

    return wrapper


def build_graph(deps: GraphDeps) -> Any:
    async def intake(state: State) -> dict[str, Any]:
        if state.get("opted_out"):
            return _flag(state, "OPTED_OUT")
        if state.get("human_active"):
            return _flag(state, "HUMAN_ACTIVE")
        if state.get("agent_paused"):
            return _flag(state, "AGENT_PAUSED")
        return {}

    async def precheck(state: State) -> dict[str, Any]:
        text = state.get("incoming_text", "")
        if state.get("media_type"):
            return _flag(state, "MEDIA_RECEIVED")
        policy = _policy(state)
        found = keywords.detect_flags(text, policy.extra_handoff_keywords if policy else None)
        reason = keywords.top_reason(found)
        if reason:
            return _flag(state, reason)
        if policy is None:
            return _flag(state, "POLICY_MISSING")
        if "ai_question" in found:
            return {"reply": {"template_id": "ai_identity_answer", "brand": "our store"}}
        if keywords.is_greeting_only(text):
            return {"reply": {"template_id": "ask_order_ref"}}
        return {}

    async def classify(state: State) -> dict[str, Any]:
        text = redact_for_llm(state.get("incoming_text", ""))
        history = "\n".join(
            f"{h['sender']}: {redact_for_llm(h['content'], 400)}" for h in state.get("history", [])
        )
        user = (
            f"{wrap_untrusted('history', history or '(none)', 3000)}\n"
            f"{wrap_untrusted('customer_message', text, 2000)}"
        )
        system = split_prompt(load_prompt(CLASSIFY_PROMPT))
        try:
            result = await deps.llm.complete_json(
                tenant_id=uuid.UUID(state["tenant_id"]),
                run_id=_uuid(state.get("run_id")),
                purpose="classify",
                system=system,
                user=user,
                schema=Classification,
                prompt_version=CLASSIFY_VERSION,
            )
        except LlmUnavailable:
            return _flag(state, "LLM_UNAVAILABLE")
        except LlmInvalid:
            return _flag(state, "INVALID_LLM_OUTPUT")
        return {"classification": result.model_dump(mode="json")}

    async def rules(state: State) -> dict[str, Any]:
        if state.get("reply"):
            return {}
        raw = state.get("classification")
        if raw is None:
            return {}
        c = Classification.model_validate(raw)
        found = {
            name
            for name, on in (
                ("human", c.flags.wants_human),
                ("legal", c.flags.mentions_legal),
                ("medical", c.flags.mentions_medical),
                ("fraud", c.flags.mentions_fraud),
                ("press", c.flags.mentions_press),
                ("safety", c.flags.mentions_safety),
                ("angry", c.sentiment == "angry"),
            )
            if on
        }
        reason = keywords.top_reason(found)
        if reason:
            return _flag(state, reason)
        if c.flags.asks_if_human:
            return {"reply": {"template_id": "ai_identity_answer", "brand": "our store"}}
        if c.confidence < deps.classify_min_confidence:
            return _flag(state, "LOW_CONFIDENCE")
        if c.mixed_intents:
            return _flag(state, "MIXED_INTENT")
        ref = c.order_ref or keywords.extract_order_ref(state.get("incoming_text", ""))
        if ref and ref in state.get("prior_customer_refs", []):
            return _flag(state, "REPEAT_COMPLAINT")
        if c.intent == "other":
            if keywords.is_greeting_only(state.get("incoming_text", "")):
                return {"reply": {"template_id": "ask_order_ref"}}
            return _flag(state, "UNSUPPORTED_INTENT")
        return {}

    async def identity(state: State) -> dict[str, Any]:
        if state.get("reply"):
            return {}
        c = Classification.model_validate(state["classification"])
        ref = (
            c.order_ref
            or keywords.extract_order_ref(state.get("incoming_text", ""))
            or state.get("active_order_ref")
        )
        attempts = int(state.get("identity", {}).get("attempts", 0))
        base = {"attempts": attempts, "verified": False, "order_ref": ref}
        if ref is None:
            return {"identity": base, "reply": {"template_id": "ask_order_ref"}}
        return {"identity": base}

    async def tools(state: State) -> dict[str, Any]:
        if state.get("reply"):
            return {}
        policy = _policy(state)
        enabled = set(policy.enabled_tools) if policy else set()
        ident = dict(state["identity"])
        ref = str(ident["order_ref"])
        if "order_lookup" not in enabled:
            return _flag(state, "TOOL_FAILED")
        order_result = await deps.tools.order_lookup(
            ref, state.get("customer_phone"), state.get("customer_email")
        )
        results: dict[str, dict[str, Any]] = {}
        if not order_result.ok:
            if order_result.error_code in ("not_found", "customer_mismatch"):
                ident["attempts"] = int(ident["attempts"]) + 1
                if ident["attempts"] >= MAX_IDENTITY_ATTEMPTS:
                    return {**_flag(state, "IDENTITY_FAILED"), "identity": ident}
                return {"identity": ident, "reply": {"template_id": "order_not_found_reask"}}
            return _flag(state, "TOOL_FAILED")
        if order_result.order is None:
            return _flag(state, "TOOL_FAILED")
        ident.update({"verified": True, "attempts": 0})
        results["order"] = order_result.order.model_dump(mode="json")
        c = Classification.model_validate(state["classification"])
        template_id = "order_status"
        if (
            c.intent == "tracking"
            and "tracking_lookup" in enabled
            and order_result.order.tracking_numbers
        ):
            tracked = await _track(deps.tools, order_result.order)
            if tracked.ok and tracked.tracking is not None:
                results["tracking"] = tracked.tracking.model_dump(mode="json")
                template_id = "tracking_status"
            elif tracked.error_code in ("upstream_error", "auth_error"):
                return _flag(state, "TOOL_FAILED")
        return {
            "identity": ident,
            "tool_results": results,
            "reply": {"template_id": template_id},
        }

    async def compose(state: State) -> dict[str, Any]:
        if state.get("flags") or state.get("reply") is None:
            return {}
        return {"reply": build_reply(state).model_dump(mode="json")}

    async def validate_node(state: State) -> dict[str, Any]:
        if state.get("flags"):
            return {}
        pending = state.get("reply")
        if pending is None:
            return {}
        spec = ReplySpec.model_validate(pending)
        policy = _policy(state)
        variants = disclosure_variants(
            policy.ai_disclosure.model_dump() if policy else None, DEFAULT_DISCLOSURE.model_dump()
        )
        problem = validate_reply(
            spec.template_id, spec.text, state.get("tool_results", {}), variants
        )
        if problem:
            return {**_flag(state, "REPLY_VALIDATION_FAILED"), "_note": problem}
        return {"reply": spec.model_dump(mode="json")}

    async def decide(state: State) -> dict[str, Any]:
        flags = state.get("flags", [])
        if not flags:
            reply = state.get("reply")
            if reply is None:
                decision = _handoff(state, "UNSUPPORTED_INTENT")
            else:
                decision = DecisionOut(action="reply", reply=ReplySpec.model_validate(reply))
            return {"decision": decision.model_dump(mode="json")}
        reason = flags[0]
        if reason in SKIP_REASONS:
            return {
                "decision": DecisionOut(action="skip", reason_code=reason).model_dump(mode="json")
            }
        return {"decision": _handoff(state, reason).model_dump(mode="json")}

    def route(next_node: str) -> Callable[[State], str]:
        def router(state: State) -> str:
            if state.get("flags"):
                return "decide"
            if state.get("reply"):
                return "compose"
            return next_node

        return router

    graph = StateGraph(State)
    for name, fn in (
        ("intake", intake),
        ("precheck", precheck),
        ("classify", classify),
        ("rules", rules),
        ("identity", identity),
        ("tools", tools),
        ("compose", compose),
        ("validate_reply", validate_node),
        ("decide", decide),
    ):
        graph.add_node(name, _timed(name, fn))
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", route("precheck"), ["precheck", "decide"])
    graph.add_conditional_edges("precheck", route("classify"), ["classify", "compose", "decide"])
    graph.add_conditional_edges("classify", route("rules"), ["rules", "decide"])
    graph.add_conditional_edges("rules", route("identity"), ["identity", "compose", "decide"])
    graph.add_conditional_edges("identity", route("tools"), ["tools", "compose", "decide"])
    graph.add_conditional_edges("tools", route("compose"), ["compose", "decide"])
    graph.add_edge("compose", "validate_reply")
    graph.add_edge("validate_reply", "decide")
    graph.add_edge("decide", END)
    return graph.compile()
