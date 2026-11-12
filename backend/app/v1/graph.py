import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent import keywords
from app.agent import templates as v0_templates
from app.agent.graph import (
    ACK_BY_REASON,
    WHY_STUCK,
    _disclosure_text,
    _lang,
    _spec,
    _trace,
    _uuid,
    build_reply,
)
from app.agent.state import State
from app.contracts.contracts import OrderFacts, ToolResult, TrackingFacts
from app.core import timeutil
from app.core.masking import redact_for_llm
from app.llm.client import (
    LlmAuthError,
    LlmClient,
    LlmInvalid,
    LlmUnavailable,
    load_prompt,
    split_prompt,
)
from app.llm.guard import wrap_untrusted
from app.policy import fraud
from app.policy import tracking as tracking_policy
from app.v1 import reply as reply_layer
from app.v1 import templates as t
from app.v1.confirm import classify_confirmation_llm, parse_confirmation
from app.v1.contracts import (
    URGENT_REASONS_V1,
    ConvUpdate,
    DecisionV1,
    HandoffSummaryV1,
    Plan,
    V1Reply,
    WriteIntent,
    WriteTool,
)
from app.v1.gate import GateContext, GateOrder, resolve
from app.v1.policy import PolicyV2, parse_any
from app.v1.toolbox import V1ToolBox

GRAPH_VERSION_V1 = "v1.0"
PLAN_PROMPT = "plan.v1.md"
PLAN_VERSION = "plan.v1"
MAX_IDENTITY_ATTEMPTS = 3
MAX_CLARIFY = 2
SKIP_REASONS = frozenset({"OPTED_OUT", "HUMAN_ACTIVE"})
WHY_STUCK_V1 = {
    **WHY_STUCK,
    "ABUSE": "Customer used abusive language.",
    "THREAT": "Customer made a threat.",
    "SENTIMENT_NEGATIVE": "Customer appears very upset.",
    "CONFUSED": "The agent could not understand the customer.",
    "NO_PROGRESS": "No progress was made even after asking twice.",
    "STEP_LIMIT": "This conversation hit its action-step limit.",
    "CONFIRM_UNCLEAR": "Customer did not clearly confirm yes or no.",
    "POLICY_EXCEPTION": "The request falls outside policy, or this tool currently requires a human.",
    "LIMIT_EXCEEDED": "The amount is above the auto-approval limit.",
    "HIGH_RISK_FLAG": "A risk flag is set on this order/customer; needs human review.",
    "ACTION_FAILED": "The action did not complete or could not be verified.",
    # Decision Framework Section 3 (see app.policy.tracking):
    "DELIVERY_STUCK": "The courier scan hasn't updated for several hours; the shipment looks stuck.",
    "DELIVERY_DISPUTE": "The system shows delivered, but the customer says they didn't receive it.",
    "IDENTITY_MISMATCH": "The email/phone the customer is messaging from doesn't match the order's"
    " registered details; verify identity before proceeding.",
}
WRITE_INTENT_TOOL: dict[str, WriteTool] = {
    "address_change": "address_update",
    "cancel": "order_cancel",
    "exchange": "exchange_request",
    # Decision Framework Section 5 vs Section 6: a return (product coming back, no
    # money promised yet) is a distinct intent from a refund (money moving) - the
    # return_request tool, its gate eligibility (app.v1.gate) and its confirm/done
    # templates (app.v1.templates) already existed; the planner just never had a
    # "return" intent to route into it, so every return message fell into "refund".
    "return": "return_request",
    "refund": "refund_create",
}


# agent_actions statuses of an earlier request on the same order (runner_v1.ACTIVE_OR_DONE):
# finished vs still waiting only for the customer's own yes/no.
DONE_STATUSES = frozenset({"executed", "confirmed"})
AWAITING_ONLY = frozenset({"awaiting_customer"})

EMPATHY_SENTIMENT = -0.2
NO_EMPATHY_REASONS = frozenset({"ABUSE", "THREAT", "AGENT_PAUSED"})
EMPATHY_LINE = {
    "en": "I'm sorry for the trouble and the wait.",
    "hinglish": "Pareshani aur intezaar ke liye maafi chahta hoon.",
    "hi": "परेशानी और इंतज़ार के लिए क्षमा चाहता हूँ।",
}


def _with_empathy(state: State, reason: str, ack: dict[str, Any] | None) -> dict[str, Any] | None:
    """One short apology line for a customer who sounded unhappy (planner sentiment at or
    below mild impatience, -0.2). Only the generic handoff acks reach this (never abuse/
    threat, never a paused agent), and it goes after the AI-disclosure line, not before it.
    A plain status question with neutral sentiment gets no apology."""
    if ack is None or reason in NO_EMPATHY_REASONS:
        return ack
    plan = state.get("plan") or {}
    try:
        sentiment = float(plan.get("sentiment") or 0.0)
    except (TypeError, ValueError):
        return ack
    if sentiment > EMPATHY_SENTIMENT:
        return ack
    lang = str(ack.get("language") or "en")
    line = EMPATHY_LINE.get(lang, EMPATHY_LINE["en"])
    body = str(ack["text"])
    disclosure = f"{_disclosure_text(state, lang)}\n\n" if state.get("first_message") else ""
    if disclosure and body.startswith(disclosure):
        body = f"{disclosure}{line}\n\n{body[len(disclosure) :]}"
    else:
        body = f"{line}\n\n{body}"
    if len(body) > 1500:
        return ack
    return {**ack, "text": body, "facts_used": [*ack.get("facts_used", []), "empathy"]}


def _empathy_text(state: State, reason: str, lang: str) -> str | None:
    """Same rule as _with_empathy() (mild impatience or worse, never abuse/threat/paused),
    returned as a bare line for the email handoff template, which places it itself - under
    the "Dear <name>," salutation - instead of prepending it to the finished text."""
    if reason in NO_EMPATHY_REASONS:
        return None
    plan = state.get("plan") or {}
    try:
        sentiment = float(plan.get("sentiment") or 0.0)
    except (TypeError, ValueError):
        return None
    if sentiment > EMPATHY_SENTIMENT:
        return None
    return EMPATHY_LINE.get(lang, EMPATHY_LINE["en"])


def _email_order_ack(
    state: State,
    reason: str,
    order: OrderFacts,
    status_line: str,
    request_note: str,
    lang: str,
) -> dict[str, Any]:
    """Professional email handoff acknowledgement: salutation, the customer's request named,
    a full order-details table (all from the verified order) and what happens next."""
    empathy = _empathy_text(state, reason, lang)
    facts: dict[str, object] = {
        "greeting": t.greeting(lang, state.get("customer_name")),
        "note": f"{empathy}\n\n" if empathy else "",
        "request_note": request_note,
        "order_block": t.order_details("email", lang, order, delivery_text=status_line),
    }
    if not state.get("within_office_hours", True):
        return _reply(state, "off_hours_ack_order", opens=state.get("opens_label") or "-", **facts)
    return _reply(state, "handoff_ack_order", **facts)


@dataclass
class V1Deps:
    llm: LlmClient
    tools: V1ToolBox
    min_confidence: float = 0.7


def gate_order_to_dict(order: GateOrder) -> dict[str, Any]:
    data = asdict(order)
    data["delivered_at"] = order.delivered_at.isoformat() if order.delivered_at else None
    data["tags"] = list(order.tags)
    return data


def gate_order_from_dict(data: dict[str, Any]) -> GateOrder:
    delivered = data.get("delivered_at")
    return GateOrder(
        **{
            **data,
            "delivered_at": datetime.fromisoformat(delivered) if delivered else None,
            "tags": tuple(data.get("tags") or ()),
        }
    )


def _policy2(state: State) -> PolicyV2 | None:
    raw = state.get("policy")
    parsed = parse_any(raw) if raw else None
    return parsed if isinstance(parsed, PolicyV2) else None


def _conv(state: State) -> dict[str, Any]:
    return dict(state.get("conv") or {})


def _known_order_ref(state: State, plan: Plan) -> str | None:
    ref = (
        plan.args.order_ref
        or keywords.extract_order_ref(state.get("incoming_text", ""))
        or _conv(state).get("order_ref")
        or state.get("active_order_ref")
    )
    return str(ref) if ref else None


def _ask_order_ref(state: State, plan: Plan) -> dict[str, Any]:
    if plan.intent not in WRITE_INTENT_TOOL:
        return _v0_reply(state, "ask_order_ref")
    if state.get("channel") == "email":
        return _reply(
            state,
            "ask_order_ref_write",
            greeting=t.greeting("en", state.get("customer_name")),
        )
    return _reply(state, "ask_order_ref_write")


def _reply(state: State, template_id: str, **facts: object) -> dict[str, Any]:
    lang = _lang(state)
    text = t.render(template_id, lang, channel=state.get("channel"), **facts)
    used = sorted(facts)
    if state.get("first_message"):
        disclosure = _disclosure_text(state, lang)
        greeting = str(facts.get("greeting") or "") if state.get("channel") == "email" else ""
        if greeting and text.startswith(greeting):
            # An email opens with its own "Dear <name>," - the AI disclosure goes right under
            # it, not above it.
            text = f"{greeting}\n\n{disclosure}\n\n{text[len(greeting) :].lstrip()}"
        else:
            text = f"{disclosure}\n\n{text}"
        used = [*used, "ai_disclosure"]
    return V1Reply(template_id=template_id, language=lang, text=text, facts_used=used).model_dump(
        mode="json"
    )


def _v0_reply(state: State, template_id: str, **facts: object) -> dict[str, Any]:
    spec = _spec(state, template_id, **facts)
    return V1Reply(
        template_id=spec.template_id,
        language=spec.language,
        text=spec.text,
        facts_used=spec.facts_used,
    ).model_dump(mode="json")


def _flag(state: State, code: str, **extra: Any) -> dict[str, Any]:
    return {"flags": [*state.get("flags", []), code], **extra}


def _order(state: State) -> OrderFacts | None:
    raw = state.get("tool_results", {}).get("order")
    return OrderFacts.model_validate(raw) if raw else None


def _tracking_v1(state: State) -> TrackingFacts | None:
    raw = state.get("tool_results", {}).get("tracking")
    return TrackingFacts.model_validate(raw) if raw else None


def _handoff_status_line(order: OrderFacts, tracking: TrackingFacts | None, lang: str) -> str:
    """One line for the handoff ack: shipped-with-date / shipped-no-date / not shipped."""
    if order.fulfillment_status == "unfulfilled":
        return {
            "en": "It has not shipped yet - still being prepared.",
            "hinglish": "Abhi tak ship nahi hua hai, tayyar ho raha hai.",
            "hi": "अभी तक शिप नहीं हुआ है, तैयार हो रहा है।",
        }[lang]
    if tracking is not None:
        if tracking.expected_delivery is not None:
            when = timeutil.to_ist(tracking.expected_delivery).strftime("%d %b %Y")
            return {
                "en": f"Shipped - {tracking.status_text}. Expected delivery by {when}.",
                "hinglish": f"Ship ho chuka hai - {tracking.status_text}. Expected delivery {when} tak.",
                "hi": f"शिप हो चुका है - {tracking.status_text}। अपेक्षित डिलीवरी {when} तक।",
            }[lang]
        return {
            "en": f"Shipped - {tracking.status_text}. Exact delivery date is not available yet.",
            "hinglish": (
                f"Ship ho chuka hai - {tracking.status_text}. "
                "Exact delivery date abhi available nahi hai."
            ),
            "hi": f"शिप हो चुका है - {tracking.status_text}। सटीक डिलीवरी तिथि अभी उपलब्ध नहीं है।",
        }[lang]
    word = v0_templates.status_word(v0_templates.FULFILL, order.fulfillment_status, lang)
    return {"en": f"Status: {word}.", "hinglish": f"Status: {word}.", "hi": f"स्थिति: {word}।"}[lang]


_NOT_SHIPPED_NOTE = {
    "en": "Your order *{order}* has not shipped yet, so there is no expected delivery date right now.\n\n",
    "hinglish": (
        "Aapka order *{order}* abhi ship nahi hua hai, isliye abhi koi expected delivery date nahi hai.\n\n"
    ),
    "hi": (
        "आपका ऑर्डर *{order}* अभी शिप नहीं हुआ है, इसलिए अभी कोई अपेक्षित डिलीवरी तिथि उपलब्ध नहीं है।\n\n"
    ),
}
STATUS_ASKS = frozenset({"order_status", "tracking"})


def _status_note(order: OrderFacts, lang: str) -> str:
    """The read-only half of a mixed message ("where is my order ... if late, cancel it"),
    built only from the verified order, placed above the confirmation question. An
    unshipped order says plainly that no delivery date exists yet."""
    if order.fulfillment_status in ("unfulfilled", ""):
        return _NOT_SHIPPED_NOTE[lang].format(order=order.order_name)
    return f"*{order.order_name}*: {_handoff_status_line(order, None, lang)}\n\n"


MAX_ACK_ITEMS = 3
_CANCEL_ASK = re.compile(r"cancel|कैंसिल|रद्द", re.IGNORECASE)
_REFUND_ASK = re.compile(r"refund|money back|paisa wapas|paise wapas|रिफंड|पैसे वापस", re.IGNORECASE)
_ORDER_LABELS = {
    "en": ("Your order", "Items", "Total", "Status", "Order date", "Payment"),
    "hinglish": ("Aapka order", "Items", "Total", "Status", "Order date", "Payment"),
    "hi": ("आपका ऑर्डर", "आइटम", "कुल", "स्थिति", "ऑर्डर की तारीख", "भुगतान"),
}
_RETURN_ASK = re.compile(r"\breturn|वापस भेज|रिटर्न|wapas bhej", re.IGNORECASE)
_EXCHANGE_ASK = re.compile(r"exchange|replace|एक्सचेंज|badalna|badal do", re.IGNORECASE)
_COMPLAINT_ASK = re.compile(
    r"complain|damag|defect|broken|faulty|wrong item|शिकायत|खराब|टूटा|shikayat",
    re.IGNORECASE,
)
_REQUEST_KINDS = (
    ("cancel", _CANCEL_ASK),
    ("refund", _REFUND_ASK),
    ("return", _RETURN_ASK),
    ("exchange", _EXCHANGE_ASK),
    ("complaint", _COMPLAINT_ASK),
)
_KIND_NOUN = {
    "en": {
        "cancel": "cancellation",
        "refund": "refund",
        "return": "return",
        "exchange": "exchange",
        "complaint": "complaint",
    },
    "hinglish": {
        "cancel": "cancel",
        "refund": "refund",
        "return": "return",
        "exchange": "exchange",
        "complaint": "shikayat",
    },
    "hi": {
        "cancel": "कैंसिलेशन",
        "refund": "रिफंड",
        "return": "रिटर्न",
        "exchange": "एक्सचेंज",
        "complaint": "शिकायत",
    },
}
_JOIN_WORD = {"en": " and ", "hinglish": " aur ", "hi": " और "}
_NOTE_ONE = {
    "en": "I have noted your request regarding {kinds}.",
    "hinglish": "Maine {kinds} ki aapki request note kar li hai.",
    "hi": "मैंने {kinds} का आपका अनुरोध नोट कर लिया है।",
}
_NOTE_COMPLAINT_ONLY = {
    "en": "I have noted your complaint.",
    "hinglish": "Maine aapki shikayat note kar li hai.",
    "hi": "मैंने आपकी शिकायत नोट कर ली है।",
}
_NOTE_FALLBACK = {
    "other": {
        "en": "I have noted the rest of your request.",
        "hinglish": "Maine aapki baaki request note kar li hai.",
        "hi": "मैंने आपके अनुरोध का बाकी हिस्सा नोट कर लिया है।",
    },
    "generic": {
        "en": "I have noted your request.",
        "hinglish": "Maine aapki request note kar li hai.",
        "hi": "मैंने आपका अनुरोध नोट कर लिया है।",
    },
}


def _request_note(text: str, lang: str, fallback: str = "other") -> str:
    """Names the kind(s) of request (cancel / refund / return / exchange / complaint) from the
    customer's own words with plain keyword matching - never from the planner's free-text
    summary, which is LLM output and must not be echoed back to the customer. fallback picks
    the sentence when no kind matched: "other" for the mixed status+write message, "generic"
    for any other handoff."""
    found = [kind for kind, rx in _REQUEST_KINDS if rx.search(text)]
    if not found:
        return _NOTE_FALLBACK[fallback][lang]
    if found == ["complaint"]:
        return _NOTE_COMPLAINT_ONLY[lang]
    nouns = [_KIND_NOUN[lang][kind] for kind in found]
    return _NOTE_ONE[lang].format(kinds=_JOIN_WORD[lang].join(nouns))


def _order_block(order: OrderFacts, status_line: str, lang: str) -> str:
    """Order facts for the handoff ack, all from the verified Shopify order (the order
    gate already matched it to this customer). At most MAX_ACK_ITEMS lines are listed."""
    head, items_label, total_label, status_label, date_label, payment_label = _ORDER_LABELS[lang]
    shown = [f"{line.title} x{line.quantity}" for line in order.lines[:MAX_ACK_ITEMS]]
    extra = len(order.lines) - MAX_ACK_ITEMS
    if extra > 0:
        shown.append(f"+{extra} more")
    rows = [f"{head} *{order.order_name}*:"]
    rows.append(f"- {date_label}: {timeutil.to_ist(order.created_at).strftime('%d %b %Y')}")
    if shown:
        rows.append(f"- {items_label}: {', '.join(shown)}")
    rows.append(f"- {total_label}: *Rs {v0_templates.paise_to_rs(order.total_paise)}*")
    rows.append(
        f"- {payment_label}: {v0_templates.status_word(v0_templates.PAY, order.financial_status, lang)}"
    )
    rows.append(f"- {status_label}: {status_line}")
    return "\n".join(rows)


async def _status_facts_for_mixed_handoff(deps: V1Deps, state: State) -> dict[str, Any] | None:
    """Best-effort order/tracking fetch for the mixed-intent handoff case only (a message
    that combines a read-only ask - order status/tracking - with a write ask such as
    cancel/refund; plan.v1.md's prompt always classifies that combination as intent
    "other" + action "handoff", so it never reaches identity()/answer() and the customer
    used to get zero order information in the ack, just "passed to our team").

    This mirrors identity()'s own order lookup (same order_gate call, same order_ref
    extraction) purely so handoff_decision() can show the status in the ack - it never
    turns into a reply/clarify turn. Any failure (no order ref found in the text, order
    not found, customer/order mismatch, tool disabled or down) just means no extra
    facts are added and the handoff proceeds exactly as it did before this existed.
    """
    policy = _policy2(state)
    if policy is None or "order_lookup" not in policy.enabled_tools:
        return None
    plan_ref = ((state.get("plan") or {}).get("args") or {}).get("order_ref")
    ref = (
        keywords.extract_order_ref(state.get("incoming_text", ""))
        or (keywords.extract_order_ref(str(plan_ref)) if plan_ref else None)
        or state.get("active_order_ref")
    )
    if ref is None:
        return None
    result, _gate = await deps.tools.order_gate(
        ref, state.get("customer_phone"), state.get("customer_email")
    )
    if not result.ok or result.order is None:
        return None
    results: dict[str, Any] = {"order": result.order.model_dump(mode="json")}
    if "tracking_lookup" in policy.enabled_tools and result.order.tracking_numbers:
        tracked = await deps.tools.tracking_lookup(
            result.order.tracking_numbers[0], result.order.courier
        )
        if tracked.ok and tracked.tracking is not None:
            results["tracking"] = tracked.tracking.model_dump(mode="json")
    return {
        "tool_results": results,
        "tool_steps": int(state.get("tool_steps", 0)) + 1,
        "handoff_order_hint": True,
    }


def handoff_decision(state: State, reason: str) -> DecisionV1:
    plan = state.get("plan") or {}
    asked = plan.get("summary") or redact_for_llm(state.get("incoming_text", ""), 400)
    did = [f"{c['tool']}: {'ok' if c['ok'] else c['error']}" for c in state.get("tool_log", [])]
    gate = state.get("gate_ctx", {}).get("last_reason")
    if gate:
        did.append(f"gate: {gate}")
    lang = _lang(state)
    order = _order(state)
    order_items = (
        ", ".join(f"{line.title} x{line.quantity}" for line in order.lines)[:200]
        if order and order.lines
        else None
    )
    summary = HandoffSummaryV1(
        customer_masked_phone=state.get("customer_masked"),
        order_ref=(state.get("identity") or {}).get("order_ref")
        or (order.order_name if order else None),
        order_items=order_items,
        customer_asked=str(asked)[:400] or "-",
        agent_did=did[:10],
        reason_code=reason,
        why_stuck=WHY_STUCK_V1.get(reason, "Needs human assistance."),
        language=lang,
    )
    ack: dict[str, Any] | None = None
    policy = _policy2(state)
    template_id = ACK_BY_REASON.get(reason, "handoff_ack")
    # Mixed-intent handoff (order status/tracking asked alongside a cancel/refund/etc.
    # request): _status_facts_for_mixed_handoff() in rules() fetched the order (and
    # tracking, if shipped) specifically so it can be shown here instead of a bare
    # "passed to our team" ack. handoff_order_hint is only ever set by that one path,
    # so every other handoff reason renders exactly as before.
    # The verified order is shown in every email/WhatsApp handoff ack (cancel, return, refund,
    # exchange and complaint cases all land here), not only in the mixed-intent case above.
    # tool_results["order"] is only ever set by a verified order lookup, so it is safe to show.
    status_order = (
        order
        if order is not None
        and (state.get("handoff_order_hint") or state.get("channel") in ("email", "whatsapp"))
        else None
    )
    email_order_ack = False
    if reason == "AGENT_PAUSED":
        ack = None
    elif template_id == "handoff_ack" and status_order is not None:
        status_line = _handoff_status_line(status_order, _tracking_v1(state), lang)
        order_block = _order_block(status_order, status_line, lang)
        request_note = _request_note(
            state.get("incoming_text", ""),
            lang,
            "other"
            if state.get("handoff_order_hint") and reason == "UNSUPPORTED_INTENT"
            else "generic",
        )
        if state.get("channel") == "email":
            email_order_ack = True
            ack = _email_order_ack(state, reason, status_order, status_line, request_note, lang)
        elif not state.get("within_office_hours", True):
            ack = _v0_reply(
                state,
                "off_hours_ack_with_status",
                opens=state.get("opens_label") or "-",
                order_block=order_block,
                request_note=request_note,
            )
        else:
            ack = _v0_reply(
                state,
                "handoff_ack_with_status",
                order_block=order_block,
                request_note=request_note,
            )
    elif template_id == "handoff_ack" and not state.get("within_office_hours", True):
        ack = _v0_reply(state, "off_hours_ack", opens=state.get("opens_label") or "-")
    elif template_id == "handoff_ack":
        ack = _v0_reply(state, "handoff_ack", sla=str(policy.handoff_sla_minutes if policy else 30))
    elif template_id == "identity_mismatch_ack":
        ack = _v0_reply(state, "identity_mismatch_ack", order=summary.order_ref or "-")
    else:
        ack = _v0_reply(state, template_id)
    if template_id == "handoff_ack" and not email_order_ack:
        ack = _with_empathy(state, reason, ack)
    return DecisionV1(
        action="handoff",
        reason_code=reason,
        priority="urgent" if reason in URGENT_REASONS_V1 else "normal",
        reply=V1Reply.model_validate(ack) if ack else None,
        handoff=summary,
        conv=ConvUpdate(clear_pending=True),
    )


def _timed(name: str, fn: Callable[[State], Awaitable[dict[str, Any]]]) -> Any:
    async def wrapper(state: State) -> dict[str, Any]:
        started = time.monotonic()
        update = await fn(state)
        update["trace"] = _trace(state, name, started, update.pop("_note", ""))
        return update

    return wrapper


def build_graph_v1(deps: V1Deps) -> Any:
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
        policy = _policy2(state)
        extra = policy.extra_handoff_keywords if policy else None
        found = keywords.detect_flags(text, extra)
        reason = keywords.top_reason(found)
        if reason:
            return _flag(state, reason)
        if policy is None:
            return _flag(state, "POLICY_MISSING")
        if "ai_question" in found:
            return {"v1_reply": _v0_reply(state, "ai_identity_answer", brand="our store")}
        if keywords.is_greeting_only(text) and _conv(state).get("awaiting", "none") == "none":
            return {"v1_reply": _v0_reply(state, "ask_order_ref")}
        return {}

    async def confirm_step(state: State) -> dict[str, Any]:
        conv = _conv(state)
        if conv.get("awaiting") != "confirm":
            return {}
        pending = conv.get("pending")
        if not pending or pending.get("expired") or pending.get("status") != "awaiting_customer":
            conv.update(awaiting="none", unclear_count=0, clear_pending=True)
            return {"conv": conv, "v1_reply": _reply(state, "confirm_expired")}
        answer = parse_confirmation(state.get("incoming_text", ""), tool=pending.get("tool"))
        if answer == "unclear":
            # Keyword matcher couldn't read it (e.g. "Yes, go ahead and cancel it." has
            # words outside the YES/NO/FILLER lists) - try the LLM fallback before giving
            # up. A real customer can phrase a yes/no reply any way at all; see
            # app.v1.confirm.classify_confirmation_llm's own docstring for why this is
            # safe (it never fires on an already-clear answer, and a failed/invalid LLM
            # call just keeps this same "unclear" - never a wrong yes/no).
            llm_answer = await classify_confirmation_llm(
                deps.llm,
                tenant_id=uuid.UUID(state["tenant_id"]),
                run_id=_uuid(state.get("run_id")),
                tool=pending.get("tool"),
                text=state.get("incoming_text", ""),
            )
            if llm_answer is not None:
                answer = llm_answer
        if answer == "unclear":
            conv["unclear_count"] = int(conv.get("unclear_count", 0)) + 1
            if conv["unclear_count"] >= 2:
                return _flag(state, "CONFIRM_UNCLEAR", conv=conv)
            return {"conv": conv, "v1_reply": _reply(state, "confirm_reask")}
        conv["unclear_count"] = 0
        if answer == "no":
            conv.update(awaiting="none", clear_pending=True)
        return {"conv": conv, "v1_confirm": answer}

    async def plan_node(state: State) -> dict[str, Any]:
        conv = _conv(state)
        text = redact_for_llm(state.get("incoming_text", ""))
        history = "\n".join(
            f"{h['sender']}: {redact_for_llm(h['content'], 400)}" for h in state.get("history", [])
        )
        known = conv.get("order_ref") or state.get("active_order_ref")
        hint = (
            f"<known_order_ref>{known}</known_order_ref>\n"
            if known and keywords.extract_order_ref(str(known))
            else ""
        )
        awaiting = conv.get("awaiting")
        if awaiting == "address":
            hint += "<awaiting>new_address_for_address_change</awaiting>\n"
        user = (
            f"{hint}{wrap_untrusted('history', history or '(none)', 3000)}\n"
            f"{wrap_untrusted('customer_message', text, 2000)}"
        )
        system = split_prompt(load_prompt(PLAN_PROMPT))
        try:
            plan = await deps.llm.complete_json(
                tenant_id=uuid.UUID(state["tenant_id"]),
                run_id=_uuid(state.get("run_id")),
                purpose="plan",
                system=system,
                user=user,
                schema=Plan,
                prompt_version=PLAN_VERSION,
            )
        except LlmAuthError:
            return _flag(state, "LLM_AUTH_ERROR")
        except LlmUnavailable:
            return _flag(state, "LLM_UNAVAILABLE")
        except LlmInvalid:
            return _flag(state, "INVALID_LLM_OUTPUT")
        return {
            "plan": plan.model_dump(mode="json"),
            "classification": {"language": plan.language, "summary": plan.summary},
        }

    async def rules(state: State) -> dict[str, Any]:
        if state.get("v1_reply"):
            return {}
        policy = _policy2(state)
        if policy is None:
            return _flag(state, "POLICY_MISSING")
        plan = Plan.model_validate(state["plan"])
        if plan.sentiment < policy.sentiment_min:
            # An upset customer (often a complaint) still gets their order details in the ack.
            order_facts = (
                await _status_facts_for_mixed_handoff(deps, state)
                if state.get("channel") != "voice"
                else None
            )
            return _flag(state, "SENTIMENT_NEGATIVE", **(order_facts or {}))
        if (
            plan.intent in WRITE_INTENT_TOOL
            and plan.action != "handoff"
            and state.get("channel") != "voice"
            and _known_order_ref(state, plan) is None
        ):
            return {}
        if plan.intent == "unclear" or plan.confidence < deps.min_confidence:
            return _flag(state, "CONFUSED")
        if state.get("channel") == "voice" and plan.intent == "address_change":
            return _flag(state, "POLICY_EXCEPTION")
        if plan.action == "handoff":
            if plan.intent in WRITE_INTENT_TOOL:
                order_facts = (
                    await _status_facts_for_mixed_handoff(deps, state)
                    if state.get("channel") != "voice"
                    else None
                )
                return _flag(state, "POLICY_EXCEPTION", **(order_facts or {}))
            if plan.intent == "other":
                # The "status question + cancel/refund in one message" case: plan.v1.md
                # always classifies this as intent "other" (two different requests, one
                # of them a write) - best-effort fetch the order so the customer gets
                # the status in the handoff ack, not just "passed to our team".
                extra = await _status_facts_for_mixed_handoff(deps, state)
                if extra:
                    return _flag(state, "UNSUPPORTED_INTENT", **extra)
            return _flag(state, "UNSUPPORTED_INTENT")
        if plan.intent == "greeting":
            return {"v1_reply": _v0_reply(state, "ask_order_ref")}
        if plan.intent == "other":
            return _flag(state, "UNSUPPORTED_INTENT")
        if plan.intent in WRITE_INTENT_TOOL:
            if plan.action not in ("propose", "clarify"):
                return _flag(state, "CONFUSED")
            if plan.action == "propose" and plan.tool != WRITE_INTENT_TOOL[plan.intent]:
                return _flag(state, "CONFUSED")
        elif plan.action != "answer":
            return _flag(state, "CONFUSED")
        return {}

    async def identity(state: State) -> dict[str, Any]:
        if state.get("v1_reply"):
            return {}
        plan = Plan.model_validate(state["plan"])
        if plan.intent == "policy_question":
            return {}
        conv = _conv(state)
        ref = _known_order_ref(state, plan)
        ident = dict(state.get("identity", {}))
        attempts = int(ident.get("attempts", 0))
        ident.update({"attempts": attempts, "verified": False, "order_ref": ref})
        if ref is None:
            conv["unclear_count"] = int(conv.get("unclear_count", 0)) + 1
            if conv["unclear_count"] >= MAX_CLARIFY + 1:
                return _flag(state, "NO_PROGRESS", identity=ident, conv=conv)
            return {"identity": ident, "conv": conv, "v1_reply": _ask_order_ref(state, plan)}
        policy = _policy2(state)
        enabled = set(policy.enabled_tools) if policy else set()
        if "order_lookup" not in enabled:
            return _flag(state, "TOOL_FAILED", identity=ident)
        result, gate = await deps.tools.order_gate(
            ref, state.get("customer_phone"), state.get("customer_email")
        )
        steps = int(state.get("tool_steps", 0)) + 1
        if not result.ok or result.order is None or gate is None:
            if result.error_code == "customer_mismatch" and plan.action == "propose":
                # Customer is asking for a write action (cancel/return/exchange/refund) on an
                # order whose registered email/phone doesn't match the sender - a dedicated
                # reason so the ack tells them exactly what to do (identity_mismatch_ack),
                # instead of the generic "passed to our team" handoff_ack HIGH_RISK_FLAG gets.
                return _flag(state, "IDENTITY_MISMATCH", identity=ident, tool_steps=steps)
            if result.error_code in ("not_found", "customer_mismatch"):
                ident["attempts"] = attempts + 1
                if ident["attempts"] >= MAX_IDENTITY_ATTEMPTS:
                    return _flag(state, "IDENTITY_FAILED", identity=ident, tool_steps=steps)
                return {
                    "identity": ident,
                    "tool_steps": steps,
                    "v1_reply": _v0_reply(state, "order_not_found_reask"),
                }
            return _flag(state, "TOOL_FAILED", identity=ident, tool_steps=steps)
        ident.update({"verified": True, "attempts": 0, "order_ref": result.order.order_name})
        return {
            "identity": ident,
            "tool_steps": steps,
            "tool_results": {"order": result.order.model_dump(mode="json")},
            "gate_order": gate_order_to_dict(gate),
        }

    async def act(state: State) -> dict[str, Any]:
        if state.get("v1_reply"):
            return {}
        plan = Plan.model_validate(state["plan"])
        policy = _policy2(state)
        if policy is not None and plan.intent == "policy_question":
            return await policy_answer(state, policy)
        order = _order(state)
        raw_gate = state.get("gate_order")
        if policy is None or order is None or raw_gate is None:
            return _flag(state, "TOOL_FAILED")
        if int(state.get("tool_steps", 0)) > policy.max_tool_steps:
            return _flag(state, "STEP_LIMIT")
        conv = _conv(state)
        if plan.action == "answer":
            return await answer(state, plan, order, policy)
        if plan.action == "clarify":
            return clarify(state, plan, order, conv)
        return propose(state, plan, order, gate_order_from_dict(raw_gate), policy, conv)

    async def answer(
        state: State, plan: Plan, order: OrderFacts, policy: PolicyV2
    ) -> dict[str, Any]:
        if plan.intent == "tracking" and tracking_policy.is_delivered_dispute(
            state.get("incoming_text", "")
        ):
            # Decision Framework Section 3's named special case: "delivered dikha raha hai,
            # lekin mujhe mila nahi" is a dispute, not a status question - it is never
            # answered with the system's own "delivered" status, always a human
            # investigation (step 5: never a refund/replacement promise before that).
            return _flag(state, "DELIVERY_DISPUTE")
        enabled = set(policy.enabled_tools)
        results: dict[str, Any] = {"order": order.model_dump(mode="json")}
        template_id = "order_status"
        if plan.intent == "tracking" and "tracking_lookup" in enabled and order.tracking_numbers:
            tracked: ToolResult = await deps.tools.tracking_lookup(
                order.tracking_numbers[0], order.courier
            )
            steps = int(state.get("tool_steps", 0)) + 1
            if tracked.ok and tracked.tracking is not None:
                if tracking_policy.stuck_in_transit(tracked.tracking, timeutil.utc_now()):
                    # Section 3: "if there is no movement, raise a courier investigation
                    # ticket" - the courier's own last scan is stale, so this is handed to
                    # a human (with a reference number, app.handoffs.reference_code)
                    # instead of being answered as a plain status.
                    return _flag(state, "DELIVERY_STUCK", tool_steps=steps)
                results["tracking"] = tracked.tracking.model_dump(mode="json")
                template_id = "tracking_status"
            elif tracked.error_code in ("upstream_error", "auth_error"):
                return _flag(state, "TOOL_FAILED", tool_steps=steps)
        elif (
            plan.intent == "order_status"
            and "tracking" in plan.secondary_answers
            and "tracking_lookup" in enabled
            and order.tracking_numbers
        ):
            # Section 5 Phase 1: customer asked both "order kaha hai" and "tracking
            # kya hai" together. Unlike the primary-tracking branch above, a failed
            # or stuck-in-transit lookup here is NOT grounds to flag/handoff the
            # whole reply - the order_status half is still a perfectly good answer
            # on its own, so a secondary enrichment that doesn't cleanly succeed is
            # just silently dropped rather than escalated.
            secondary_steps = int(state.get("tool_steps", 0)) + 1
            tracked_secondary: ToolResult = await deps.tools.tracking_lookup(
                order.tracking_numbers[0], order.courier
            )
            if tracked_secondary.ok and tracked_secondary.tracking is not None:
                results["tracking"] = tracked_secondary.tracking.model_dump(mode="json")
                state = {**state, "tool_steps": secondary_steps}
        merged: State = {**state, "tool_results": results, "reply": {"template_id": template_id}}
        fallback = build_reply(merged)
        facts: dict[str, Any] = {"order": reply_layer.order_facts(results["order"], _lang(state))}
        if "tracking" in results:
            facts["tracking"] = reply_layer.tracking_facts(results["tracking"])
        if "policy_question" in plan.secondary_answers:
            # The customer's other read-only question in the same message. Order
            # facts need the identity/order-gate verification that already ran
            # for this primary intent; policy facts need none of that, so this
            # combination is always safe to add regardless of which of
            # order_status/tracking is primary.
            facts["policy"] = reply_layer.policy_facts(policy)
        composed = await compose_reply(state, policy, facts)
        update: dict[str, Any] = {"tool_results": results, **composed["meta"]}
        if composed["reply"] is not None:
            update["v1_reply"] = composed["reply"]
        elif composed["needs_human"]:
            return {**_flag(state, "UNSUPPORTED_INTENT"), **update}
        else:
            update["v1_reply"] = V1Reply(
                template_id=fallback.template_id,
                language=fallback.language,
                text=fallback.text,
                facts_used=fallback.facts_used,
            ).model_dump(mode="json")
        return update

    async def policy_answer(state: State, policy: PolicyV2) -> dict[str, Any]:
        # Section 5 Phase 1 scope: when policy_question is the PRIMARY intent,
        # identity() skips order lookup entirely (see identity()'s early return
        # for policy_question above) - there is no verified order in state to
        # safely attach if the customer also asked an order_status/tracking
        # question in the same message. Making that combination work would mean
        # identity() running the order-gate lookup (with its full not-found /
        # customer-mismatch / attempts-limit handling) even for a policy-primary
        # message, which is a real change to the identity-verification gate, not
        # just an extra fact to merge in - left for a later phase. The prompt
        # (plan.v1.md) is written to prefer order_status/tracking as the primary
        # intent whenever one is present, specifically so this combination is
        # rare; if the planner still emits it, plan.secondary_answers is simply
        # not read here and the policy question still gets answered on its own.
        composed = await compose_reply(state, policy, {"policy": reply_layer.policy_facts(policy)})
        if composed["reply"] is not None:
            return {**composed["meta"], "v1_reply": composed["reply"]}
        if composed["needs_human"]:
            return {**_flag(state, "UNSUPPORTED_INTENT"), **composed["meta"]}
        return {**_flag(state, "REPLY_VALIDATION_FAILED"), **composed["meta"]}

    async def compose_reply(
        state: State, policy: PolicyV2 | None, facts: dict[str, Any]
    ) -> dict[str, Any]:
        lang = _lang(state)
        history = "\n".join(
            f"{h['sender']}: {redact_for_llm(h['content'], 400)}" for h in state.get("history", [])
        )
        outcome = await reply_layer.compose(
            deps.llm,
            tenant_id=uuid.UUID(state["tenant_id"]),
            run_id=_uuid(state.get("run_id")),
            facts=facts,
            customer_text=state.get("incoming_text", ""),
            history=history,
            language=lang,
            tone_notes=policy.tone_notes if policy else "",
        )
        meta = {
            "reply_attempts": outcome.attempts,
            "reply_source": "llm" if outcome.draft else "template",
        }
        if outcome.draft is None:
            return {"reply": None, "needs_human": outcome.needs_human, "meta": meta}
        text = outcome.draft.text
        used = list(outcome.draft.facts_used)
        if state.get("first_message"):
            text = f"{_disclosure_text(state, lang)}\n\n{text}"
            used.append("ai_disclosure")
        reply = V1Reply(template_id="llm_reply", language=lang, text=text, facts_used=used)
        return {"reply": reply.model_dump(mode="json"), "needs_human": False, "meta": meta}

    def clarify(
        state: State, plan: Plan, order: OrderFacts, conv: dict[str, Any]
    ) -> dict[str, Any]:
        conv["unclear_count"] = int(conv.get("unclear_count", 0)) + 1
        conv["order_ref"] = order.order_name
        if conv["unclear_count"] > MAX_CLARIFY:
            return _flag(state, "NO_PROGRESS", conv=conv)
        if plan.intent == "address_change":
            conv["awaiting"] = "address"
            return {"conv": conv, "v1_reply": _reply(state, "ask_address")}
        conv["awaiting"] = "none"
        if plan.intent == "exchange":
            return {
                "conv": conv,
                "v1_reply": _reply(state, "ask_line_item", order=order.order_name),
            }
        return {"conv": conv, "v1_reply": _reply(state, "ask_clarify")}

    def propose(
        state: State,
        plan: Plan,
        order: OrderFacts,
        gate_order: GateOrder,
        policy: PolicyV2,
        conv: dict[str, Any],
    ) -> dict[str, Any]:
        tool = plan.tool
        if tool is None:
            return _flag(state, "CONFUSED")
        if tool not in policy.enabled_tools:
            return _flag(state, "POLICY_EXCEPTION")
        # Omnichannel duplicate guard: this same customer (across every channel they've
        # used - see app.channels.identity.link_customer_identities) already has this
        # exact tool+order in flight on a DIFFERENT conversation (e.g. they emailed a
        # cancel request, then WhatsApped the same thing). Don't start a second one -
        # tell them it's already being handled instead of silently proposing/gating again.
        same_request = [
            claim
            for claim in state.get("active_claims", [])
            if claim.get("tool") == tool
            and claim.get("order_ref") == order.order_name
            and claim.get("conversation_id") != state.get("conversation_id")
        ]
        if same_request:
            # The reply follows the earlier action's real status: finished, waiting for the
            # customer's own yes/no, or genuinely with the team. It never says "our team is
            # working on it" for something that is already done or was never handed over.
            statuses = {str(claim.get("status")) for claim in same_request}
            if statuses & DONE_STATUSES:
                dup_template, dup_status = "duplicate_request_done", "completed"
            elif statuses - AWAITING_ONLY - DONE_STATUSES:
                dup_template, dup_status = "duplicate_request_ack", "processing"
            else:
                dup_template, dup_status = "duplicate_request_awaiting", "awaiting"
            dup_lang = _lang(state)
            dup_facts: dict[str, object] = {
                "order": order.order_name,
                "brand": state.get("tenant_name") or "Support",
                "request": t.REQUEST_NOUN.get(dup_lang, t.REQUEST_NOUN["en"])[tool],
            }
            dup_facts.update(
                t.detail_facts(
                    state.get("channel"),
                    dup_lang,
                    tool,
                    order,
                    dup_facts,
                    dup_status,
                    greet_name=state.get("customer_name"),
                )
            )
            conv["awaiting"] = "none"
            return {"conv": conv, "v1_reply": _reply(state, dup_template, **dup_facts)}
        claim_tool = tool in ("refund_create", "exchange_request", "return_request")
        if claim_tool and fraud.requires_evidence(plan.args.reason):
            # Decision Framework Section 12.3: photo/video evidence is mandatory before any
            # refund, replacement or exchange is promised for this claim category - the
            # written claim alone is never enough, and the agent has no automated way to
            # check a photo. So it never auto-proposes this write: it asks for the photo,
            # and the moment one arrives the MEDIA_RECEIVED precheck above hands the whole
            # case - claim text and photo together - to a human. Repeated asking without a
            # photo ever arriving converges to the same handoff via MAX_CLARIFY below.
            conv["unclear_count"] = int(conv.get("unclear_count", 0)) + 1
            conv["order_ref"] = order.order_name
            if conv["unclear_count"] > MAX_CLARIFY:
                conv["awaiting"] = "none"
                return _flag(state, "POLICY_EXCEPTION", conv=conv)
            conv["awaiting"] = "evidence"
            return {"conv": conv, "v1_reply": _reply(state, "ask_evidence_photo")}
        args: dict[str, Any] = {"reason": plan.args.reason}
        item = ""
        if tool == "exchange_request":
            wanted = (plan.args.line_item or "").lower()
            match = next(
                (
                    ln.title
                    for ln in order.lines
                    if wanted and (wanted in ln.title.lower() or ln.title.lower() in wanted)
                ),
                None,
            )
            if match is None:
                conv["unclear_count"] = int(conv.get("unclear_count", 0)) + 1
                conv["order_ref"] = order.order_name
                if conv["unclear_count"] > MAX_CLARIFY:
                    return _flag(state, "NO_PROGRESS", conv=conv)
                return {
                    "conv": conv,
                    "v1_reply": _reply(state, "ask_line_item", order=order.order_name),
                }
            item = match
            args.update(line_item=match, new_variant=plan.args.new_variant)
        if tool == "address_update" and plan.args.new_address is not None:
            args["new_address"] = plan.args.new_address.model_dump(mode="json")
        prior = state.get("prior_writes", [])
        done = {(p["tool"], p["order_ref"]) for p in prior}
        gate_order = GateOrder(
            **{
                **gate_order.__dict__,
                "address_already_changed": ("address_update", order.order_name) in done,
                "exchange_already_requested": ("exchange_request", order.order_name) in done
                or gate_order.exchange_already_requested,
                "return_already_requested": ("return_request", order.order_name) in done
                or gate_order.return_already_requested,
            }
        )
        ctx_raw = state.get("gate_ctx", {})
        ctx = GateContext(
            tool=tool,
            order=gate_order,
            now=timeutil.utc_now(),
            customer_refunded_today_paise=int(ctx_raw.get("refunded_today", 0)),
            customer_refund_in_last_30d=bool(ctx_raw.get("recent_refund", False)),
            brand_auto_value_today_paise=int(ctx_raw.get("brand_today", 0)),
            killed=bool(ctx_raw.get("killed", {}).get(tool, False)),
            write_count=int(conv.get("write_count", 0)),
            channel="voice" if state.get("channel") == "voice" else "text",
            recent_claim_count=int(ctx_raw.get("recent_claims", 0)),
        )
        decision = resolve(policy, ctx)
        gate_note = {"gate_ctx": {**ctx_raw, "last_reason": decision.reason}}
        if decision.route == "handoff":
            return _flag(state, decision.handoff_reason or "POLICY_EXCEPTION", **gate_note)
        if decision.route == "decline":
            conv.update(awaiting="none", unclear_count=0, order_ref=order.order_name)
            decline_facts: dict[str, object] = {
                "order": order.order_name,
                "days": policy.return_window_days,
            }
            decline_facts.update(
                t.detail_facts(
                    state.get("channel"),
                    _lang(state),
                    tool,
                    order,
                    decline_facts,
                    greet_name=state.get("customer_name"),
                )
            )
            return {
                **gate_note,
                "conv": conv,
                "v1_reply": _reply(state, t.DECLINE_TEMPLATE[decision.reason], **decline_facts),
            }
        intent = WriteIntent(
            tool=tool,
            order_ref=order.order_name,
            amount_paise=decision.amount_paise,
            route=decision.route,
            gate_reason=decision.reason,
            args={k: v for k, v in args.items() if v is not None},
        )
        lang = _lang(state)
        note = t.render("approval_note" if decision.route == "approval" else "no_note", lang)
        if state.get("channel") != "voice" and STATUS_ASKS.intersection(plan.secondary_answers):
            note = _status_note(order, lang) + note
        if plan.sentiment < 0:
            # Already past rules()'s SENTIMENT_NEGATIVE handoff check (plan.sentiment >=
            # policy.sentiment_min there), so any remaining negative score here is a
            # described delay/problem, not anger that already went to a human.
            note = t.render("empathy_note", lang) + note
        items = ", ".join(f"{ln.title} x{ln.quantity}" for ln in order.lines)[:120] or "-"
        facts: dict[str, object] = {
            "order": order.order_name,
            "note": note,
            "brand": state.get("tenant_name") or "Support",
        }
        if tool == "order_cancel":
            facts.update(items=items, amount=t.rs(decision.amount_paise))
        elif tool == "refund_create":
            facts.update(amount=t.rs(decision.amount_paise))
        elif tool == "exchange_request":
            facts.update(item=item, variant=plan.args.new_variant or "-")
        elif tool == "return_request":
            pass
        else:
            facts.update(address=_address_text(args["new_address"]))
        facts.update(
            t.detail_facts(
                state.get("channel"),
                lang,
                tool,
                order,
                facts,
                greet_name=state.get("customer_name"),
            )
        )
        conv.update(awaiting="confirm", unclear_count=0, order_ref=order.order_name)
        return {
            **gate_note,
            "conv": conv,
            "v1_intent": intent.model_dump(mode="json"),
            "v1_reply": _reply(state, f"confirm_{tool}", **facts),
        }

    async def decide(state: State) -> dict[str, Any]:
        conv = _conv(state)
        flags = state.get("flags", [])
        if flags:
            reason = flags[0]
            if reason in SKIP_REASONS:
                decision = DecisionV1(action="skip", reason_code=reason)
            else:
                decision = handoff_decision(state, reason)
                decision.conv = ConvUpdate(
                    awaiting="none",
                    unclear_count=0,
                    turn_count=int(conv.get("turn_count", 0)) + 1,
                    clear_pending=True,
                )
            return {"decision_v1": decision.model_dump(mode="json")}
        reply = state.get("v1_reply")
        intent = state.get("v1_intent")
        confirm = state.get("v1_confirm")
        if reply is None and intent is None and confirm is None:
            return {
                "decision_v1": handoff_decision(state, "UNSUPPORTED_INTENT").model_dump(mode="json")
            }
        decision = DecisionV1(
            action="reply",
            reply=V1Reply.model_validate(reply) if reply else None,
            intent=WriteIntent.model_validate(intent) if intent else None,
            confirm=confirm,
            conv=ConvUpdate(
                awaiting=conv.get("awaiting", "none"),
                unclear_count=int(conv.get("unclear_count", 0)),
                turn_count=int(conv.get("turn_count", 0)) + 1,
                clear_pending=bool(conv.get("clear_pending", False)),
                order_ref=conv.get("order_ref"),
            ),
        )
        return {"decision_v1": decision.model_dump(mode="json")}

    def route(next_node: str) -> Callable[[State], str]:
        def router(state: State) -> str:
            if state.get("flags") or state.get("v1_reply") or state.get("v1_confirm"):
                return "decide"
            return next_node

        return router

    graph = StateGraph(State)
    for name, fn in (
        ("intake", intake),
        ("precheck", precheck),
        ("confirm_step", confirm_step),
        ("plan", plan_node),
        ("rules", rules),
        ("identity", identity),
        ("act", act),
        ("decide", decide),
    ):
        graph.add_node(name, _timed(name, fn))
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", route("precheck"), ["precheck", "decide"])
    graph.add_conditional_edges("precheck", route("confirm_step"), ["confirm_step", "decide"])
    graph.add_conditional_edges("confirm_step", route("plan"), ["plan", "decide"])
    graph.add_conditional_edges("plan", route("rules"), ["rules", "decide"])
    graph.add_conditional_edges("rules", route("identity"), ["identity", "decide"])
    graph.add_conditional_edges("identity", route("act"), ["act", "decide"])
    graph.add_edge("act", "decide")
    graph.add_edge("decide", END)
    return graph.compile()


def _address_text(address: dict[str, Any]) -> str:
    parts = [address.get("line1"), address.get("line2"), address.get("city"), address.get("state")]
    line = ", ".join(str(p) for p in parts if p)
    return f"{line} - {address.get('pin')}"
