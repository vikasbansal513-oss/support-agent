import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent import keywords
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
from app.agent.validate import pick_language
from app.contracts.contracts import OrderFacts, ToolResult
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
from app.v1.confirm import parse_confirmation
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
from app.v1.gate import GateContext, GateOrder, evaluate
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
    "ABUSE": "Customer ne gaali ya badtameezi ki.",
    "THREAT": "Customer ne dhamki di.",
    "SENTIMENT_NEGATIVE": "Customer bahut naraaz lag raha hai.",
    "CONFUSED": "Agent customer ki baat samajh nahi paya.",
    "NO_PROGRESS": "Do baar poochne par bhi kaam aage nahi badha.",
    "STEP_LIMIT": "Ek baatcheet me action ki limit poori ho gayi.",
    "CONFIRM_UNCLEAR": "Customer ne confirm ka haan ya na saaf nahi kiya.",
    "POLICY_EXCEPTION": "Request policy ke bahar hai ya is tool par abhi insaan chahiye.",
    "LIMIT_EXCEEDED": "Amount auto limit ke upar hai.",
    "HIGH_RISK_FLAG": "Order ya customer par risk flag hai, insaan dekhe.",
    "ACTION_FAILED": "Action poora nahi ho paya ya verify nahi hua.",
    # Decision Framework Section 3 (see app.policy.tracking):
    "DELIVERY_STUCK": "Shipment ka courier scan kai ghante se update nahi hua, lagta hai atka hua hai.",
    "DELIVERY_DISPUTE": "System delivered dikha raha hai, customer kehta hai usse mila nahi.",
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


def _reply(state: State, template_id: str, **facts: object) -> dict[str, Any]:
    lang = _lang(state)
    text = t.render(template_id, lang, **facts)
    used = sorted(facts)
    if state.get("first_message"):
        text = f"{_disclosure_text(state, lang)}\n\n{text}"
        used = [*used, "ai_disclosure"]
    return V1Reply(
        template_id=template_id, language=pick_language(lang), text=text, facts_used=used
    ).model_dump(mode="json")


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


def handoff_decision(state: State, reason: str) -> DecisionV1:
    plan = state.get("plan") or {}
    asked = plan.get("summary") or redact_for_llm(state.get("incoming_text", ""), 400)
    did = [f"{c['tool']}: {'ok' if c['ok'] else c['error']}" for c in state.get("tool_log", [])]
    gate = state.get("gate_ctx", {}).get("last_reason")
    if gate:
        did.append(f"gate: {gate}")
    lang = pick_language(_lang(state))
    summary = HandoffSummaryV1(
        customer_masked_phone=state.get("customer_masked"),
        order_ref=(state.get("identity") or {}).get("order_ref"),
        customer_asked=str(asked)[:400] or "-",
        agent_did=did[:10],
        reason_code=reason,
        why_stuck=WHY_STUCK_V1.get(reason, "Insaan ki madad chahiye."),
        language=lang,
    )
    ack: dict[str, Any] | None = None
    policy = _policy2(state)
    template_id = ACK_BY_REASON.get(reason, "handoff_ack")
    if reason == "AGENT_PAUSED":
        ack = None
    elif template_id == "handoff_ack" and not state.get("within_office_hours", True):
        ack = _v0_reply(state, "off_hours_ack", opens=state.get("opens_label") or "-")
    elif template_id == "handoff_ack":
        ack = _v0_reply(state, "handoff_ack", sla=str(policy.handoff_sla_minutes if policy else 30))
    else:
        ack = _v0_reply(state, template_id)
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
        answer = parse_confirmation(state.get("incoming_text", ""))
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
            return _flag(state, "SENTIMENT_NEGATIVE")
        if plan.intent == "unclear" or plan.confidence < deps.min_confidence:
            return _flag(state, "CONFUSED")
        if state.get("channel") == "voice" and plan.intent == "address_change":
            return _flag(state, "POLICY_EXCEPTION")
        if plan.action == "handoff":
            if plan.intent in WRITE_INTENT_TOOL:
                return _flag(state, "POLICY_EXCEPTION")
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
        ref = (
            plan.args.order_ref
            or keywords.extract_order_ref(state.get("incoming_text", ""))
            or conv.get("order_ref")
            or state.get("active_order_ref")
        )
        ident = dict(state.get("identity", {}))
        attempts = int(ident.get("attempts", 0))
        ident.update({"attempts": attempts, "verified": False, "order_ref": ref})
        if ref is None:
            conv["unclear_count"] = int(conv.get("unclear_count", 0)) + 1
            if conv["unclear_count"] >= MAX_CLARIFY + 1:
                return _flag(state, "NO_PROGRESS", identity=ident, conv=conv)
            return {"identity": ident, "conv": conv, "v1_reply": _v0_reply(state, "ask_order_ref")}
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
                return _flag(state, "HIGH_RISK_FLAG", identity=ident, tool_steps=steps)
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
        merged: State = {**state, "tool_results": results, "reply": {"template_id": template_id}}
        fallback = build_reply(merged)
        facts: dict[str, Any] = {"order": reply_layer.order_facts(results["order"], _lang(state))}
        if "tracking" in results:
            facts["tracking"] = reply_layer.tracking_facts(results["tracking"])
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
        reply = V1Reply(
            template_id="llm_reply", language=pick_language(lang), text=text, facts_used=used
        )
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
        decision = evaluate(policy, ctx)
        gate_note = {"gate_ctx": {**ctx_raw, "last_reason": decision.reason}}
        if decision.route == "handoff":
            return _flag(state, decision.handoff_reason or "POLICY_EXCEPTION", **gate_note)
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
        items = ", ".join(f"{ln.title} x{ln.quantity}" for ln in order.lines)[:120] or "-"
        facts: dict[str, object] = {"order": order.order_name, "note": note}
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
