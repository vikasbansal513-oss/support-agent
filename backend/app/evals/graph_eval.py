import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agent.graph import GraphDeps, build_graph
from app.agent.tools import FakeToolBox
from app.contracts.contracts import (
    DecisionOut,
    OrderFacts,
    OrderLine,
    PolicyV1,
    ToolResult,
    TrackingFacts,
)
from app.evals.dataset import EvalRow
from app.llm.client import LlmClient
from app.llm.provider import FakeProvider

REASON_BY_FLAG = {
    "safety": "SAFETY",
    "medical": "MEDICAL",
    "legal": "LEGAL",
    "fraud": "FRAUD",
    "injection": "FRAUD",
    "press": "PRESS",
    "angry": "ANGRY",
    "human": "HUMAN_REQUESTED",
}
PRIORITY = ["SAFETY", "MEDICAL", "LEGAL", "FRAUD", "PRESS", "ANGRY", "HUMAN_REQUESTED"]
COMPLAINT_FLAGS = frozenset(REASON_BY_FLAG)
CLASSIFY_FLAG = {
    "human": "wants_human",
    "legal": "mentions_legal",
    "medical": "mentions_medical",
    "fraud": "mentions_fraud",
    "press": "mentions_press",
    "safety": "mentions_safety",
    "ai_question": "asks_if_human",
}


@dataclass(frozen=True)
class Expected:
    action: str
    detail: str


def expected_for(row: EvalRow) -> Expected | None:
    if OPT_TAGS & set(row.tags):
        return None
    reasons = {REASON_BY_FLAG[f] for f in row.flags if f in REASON_BY_FLAG}
    for reason in PRIORITY:
        if reason in reasons:
            return Expected("handoff", reason)
    if "ai_question" in row.flags:
        return Expected("reply", "ai_identity_answer")
    if "greeting" in row.tags:
        return Expected("reply", "ask_order_ref")
    if "mixed" in row.tags:
        return Expected("handoff", "MIXED_INTENT")
    if row.intent == "other":
        return Expected("handoff", "UNSUPPORTED_INTENT")
    if row.order_ref is None:
        return Expected("reply", "ask_order_ref")
    return Expected("reply", "tracking_status" if row.intent == "tracking" else "order_status")


OPT_TAGS = {"opt_out", "opt_in"}


def oracle_classification(row: EvalRow) -> str:
    flags = {value: False for value in CLASSIFY_FLAG.values()}
    for flag in row.flags:
        if flag in CLASSIFY_FLAG:
            flags[CLASSIFY_FLAG[flag]] = True
    return json.dumps(
        {
            "intent": row.intent,
            "order_ref": row.order_ref,
            "confidence": 0.95,
            "language": row.language if row.language in ("hi", "en", "hinglish") else "hinglish",
            "sentiment": "angry" if "angry" in row.flags else "normal",
            "flags": flags,
            "mixed_intents": ["tracking", "other"] if "mixed" in row.tags else [],
            "summary": "eval",
        }
    )


def order_facts(ref: str) -> OrderFacts:
    digits = ref.lstrip("#")
    return OrderFacts(
        order_name=f"#{digits}",
        financial_status="paid",
        fulfillment_status="fulfilled",
        lines=[OrderLine(title="Kurta", quantity=1)],
        total_paise=99900,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        tracking_numbers=[f"AWB{digits}"],
        courier="Delhivery",
    )


def toolbox_for(refs: set[str]) -> FakeToolBox:
    box = FakeToolBox()
    for ref in refs:
        facts = order_facts(ref)
        box.orders[facts.order_name] = ToolResult(tool="order_lookup", ok=True, order=facts)
        awb = facts.tracking_numbers[0]
        box.tracking[awb] = ToolResult(
            tool="tracking_lookup",
            ok=True,
            tracking=TrackingFacts(awb=awb, courier="Delhivery", status_text="In Transit"),
        )
    return box


def base_state(text: str, media_type: str | None = None) -> dict[str, Any]:
    return {
        "tenant_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "incoming_text": text,
        "media_type": media_type,
        "policy": PolicyV1().model_dump(mode="json"),
        "first_message": False,
        "within_office_hours": True,
        "history": [],
        "flags": [],
        "trace": [],
        "tool_results": {},
        "identity": {"attempts": 0},
        "customer_phone": "+919876543210",
    }


async def run_state(
    llm: LlmClient, tools: FakeToolBox, state: dict[str, Any]
) -> tuple[DecisionOut, FakeToolBox]:
    graph = build_graph(GraphDeps(llm=llm, tools=tools))
    final = await graph.ainvoke(state, config={"recursion_limit": 25})
    return DecisionOut.model_validate(final["decision"]), tools


def outcome(decision: DecisionOut) -> Expected:
    if decision.action == "reply" and decision.reply is not None:
        return Expected("reply", decision.reply.template_id)
    return Expected(decision.action, decision.reason_code or "")


@dataclass
class EvalReport:
    total: int = 0
    correct: int = 0
    complaint_total: int = 0
    complaint_caught: int = 0
    failures: list[str] = field(default_factory=list)
    by_split: Counter[str] = field(default_factory=Counter)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def complaint_recall(self) -> float:
        return self.complaint_caught / self.complaint_total if self.complaint_total else 1.0


async def evaluate(rows: list[EvalRow], llm_for: Any) -> EvalReport:
    report = EvalReport()
    for row in rows:
        want = expected_for(row)
        if want is None:
            continue
        decision, _ = await run_state(
            llm_for(row), toolbox_for({r for r in [row.order_ref] if r}), base_state(row.text)
        )
        got = outcome(decision)
        report.total += 1
        report.by_split[row.split] += 1
        ok = got == want
        report.correct += 1 if ok else 0
        if row.flags & COMPLAINT_FLAGS and "ai_question" not in row.flags - COMPLAINT_FLAGS - {
            "human"
        }:
            report.complaint_total += 1
            report.complaint_caught += 1 if got.action == "handoff" else 0
        if not ok:
            report.failures.append(f"{row.id} {row.text!r}: want {want} got {got}")
    return report


def oracle_llm(row: EvalRow) -> LlmClient:
    return LlmClient(FakeProvider(responses=[oracle_classification(row)]))
